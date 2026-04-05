"""Skill runner - executes scheduled skills and optionally creates pupdates.

Scheduled skills are user-defined prompts that run on a timer,
using whatever MCPs the user has configured. This turns any
MCP-connected tool into a poller without writing Python.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def run_scheduled_skills():
    """Check for due skills and execute them.

    Called from the brain cycle to run any skills whose interval has elapsed.

    Returns: list of skill names that were run
    """
    try:
        from planet_maiko.models.custom_skill import CustomSkill
    except ImportError:
        return []

    from planet_maiko.database import db

    now = datetime.now(timezone.utc)
    skills = CustomSkill.query.filter(
        CustomSkill.schedule_interval_minutes.isnot(None),
        CustomSkill.schedule_interval_minutes > 0,
    ).all()

    ran = []
    for skill in skills:
        interval = timedelta(minutes=skill.schedule_interval_minutes)
        last_run = skill.last_run_at

        if last_run and (now - last_run) < interval:
            continue  # Not due yet

        try:
            from planet_maiko.agents.brain_session import run_skill, _get_runtime
            runtime = _get_runtime()
            if not runtime or not runtime.is_available():
                continue

            # Build minimal context
            from planet_maiko.models.pupdate import Pupdate
            from planet_maiko.models.task import Task
            pupdates = Pupdate.query.filter_by(dismissed=False).order_by(Pupdate.timestamp.desc()).limit(15).all()
            tasks = Task.query.filter(Task.status.in_(["new", "in_progress"])).limit(15).all()

            context = {
                "pupdates": json.dumps([p.to_dict() for p in pupdates]),
                "tasks": json.dumps([t.to_dict() for t in tasks]),
                "calendar": "[]",
                "query": "",
                "context": "",
            }

            result = run_skill(skill.id, context=context)
            skill.last_run_at = now

            # Create pupdate from output if configured
            if skill.creates_pupdates and result and result.get("success"):
                count = parse_skill_output_to_pupdates(skill, result["output"], db.session)
                if count:
                    logger.info(f"[skill_runner] Created {count} pupdate(s) from {skill.name}")

            ran.append(skill.id)
            logger.info(f"[skill_runner] Ran scheduled skill: {skill.id}")

        except Exception as e:
            logger.warning(f"[skill_runner] Failed to run {skill.id}: {e}")

    if ran:
        db.session.commit()

    return ran


def run_scheduled_skill(skill_id, app):
    """Execute a scheduled skill and save the result.

    Args:
        skill_id: the CustomSkill ID to run
        app: Flask app (for app context)
    """
    with app.app_context():
        from planet_maiko.database import db
        from planet_maiko.models.custom_skill import CustomSkill
        from planet_maiko.models.skill_result import SkillResult
        from planet_maiko.agents.brain_session import run_skill

        skill = db.session.get(CustomSkill, skill_id)
        if not skill:
            logger.warning(f"[skill-runner] Skill {skill_id} not found")
            return

        # Gather context
        from planet_maiko.models.pupdate import Pupdate
        from planet_maiko.models.task import Task

        pupdates = Pupdate.query.filter_by(dismissed=False).order_by(Pupdate.timestamp.desc()).limit(20).all()
        tasks = Task.query.filter(Task.status.in_(["new", "in_progress"])).all()

        context = {
            "pupdates": json.dumps([p.to_dict() for p in pupdates]),
            "tasks": json.dumps([t.to_dict() for t in tasks]),
            "calendar": "[]",
            "query": "",
            "context": "",
        }

        logger.info(f"[skill-runner] Running scheduled skill: {skill.name}")
        result = run_skill(skill_id, context=context)

        if not result.get("success"):
            logger.warning(f"[skill-runner] Skill {skill.name} failed: {result.get('error')}")
            skill.last_run_at = datetime.now(timezone.utc)
            db.session.commit()
            return

        # Save result
        sr = SkillResult(
            skill_name=skill_id,
            title=f"{skill.name} — {datetime.now().strftime('%B %d %H:%M')}",
            content=result["output"],
        )
        db.session.add(sr)

        # Create pupdates from output if enabled
        if skill.creates_pupdates:
            count = parse_skill_output_to_pupdates(skill, result["output"], db.session)
            logger.info(f"[skill-runner] Created {count} pupdate(s) from {skill.name}")

        skill.last_run_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.info(f"[skill-runner] Completed: {skill.name}")


def parse_skill_output_to_pupdates(skill, output, db_session):
    """Parse skill output into individual pupdates.

    Strategy:
    1. If output contains JSON blocks (```json ... ```), extract pupdate dicts
    2. Otherwise, split by ## headers, create one pupdate per section
    """
    from planet_maiko.models.pupdate import Pupdate

    count = 0
    now = datetime.now(timezone.utc)

    # Strategy 1: Look for JSON blocks
    json_blocks = re.findall(r'```json\s*(.*?)\s*```', output, re.DOTALL)
    if json_blocks:
        for block in json_blocks:
            try:
                data = json.loads(block)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    pid = hashlib.sha256(f"{skill.id}:{item.get('title', '')}:{now.isoformat()}".encode()).hexdigest()[:12]
                    pupdate = Pupdate(
                        id=pid,
                        timestamp=now,
                        source="skill",
                        source_id=f"{skill.id}/{now.timestamp()}",
                        type=item.get("type", "skill_automation"),
                        priority=item.get("priority", "normal"),
                        title=item.get("title", f"[{skill.name}] Update"),
                        body=item.get("body", ""),
                        actionable=item.get("actionable", False),
                        action_hint=item.get("action_hint"),
                        tags=[skill.id, "automation"],
                    )
                    db_session.add(pupdate)
                    count += 1
            except (json.JSONDecodeError, KeyError):
                continue
        return count

    # Strategy 2: Split by ## headers
    sections = re.split(r'^## (.+)$', output, flags=re.MULTILINE)
    # sections alternates: [preamble, header1, body1, header2, body2, ...]
    for i in range(1, len(sections) - 1, 2):
        header = sections[i].strip()
        body = sections[i + 1].strip() if i + 1 < len(sections) else ""

        if not header or len(header) < 3:
            continue

        pid = hashlib.sha256(f"{skill.id}:{header}:{now.isoformat()}".encode()).hexdigest()[:12]
        pupdate = Pupdate(
            id=pid,
            timestamp=now,
            source="skill",
            source_id=f"{skill.id}/{now.timestamp()}",
            type="skill_automation",
            priority="normal",
            title=f"[{skill.name}] {header}",
            body=body[:500] if body else "",
            tags=[skill.id, "automation"],
        )
        db_session.add(pupdate)
        count += 1

    return count
