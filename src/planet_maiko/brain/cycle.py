"""Brain cycle - the clock tick that drives all processors.

Each cycle runs all phases in order, just like a CPU executes its
pipeline on each clock tick. Each phase is its own function so failures
are isolated and the orchestrator stays readable.

Pipeline (phases run in this order):
    1.  agents               — process agent pupdates (auto-complete tasks)
    1.5 auto_complete_reviews — close review tasks for approved/merged PRs
    2.  awareness            — A2A conflict detection + resolution
    2.5 calendar_focus       — auto-focus from calendar events
    3.  correlator           — group related pupdates into incidents
    3.5 pupdates             — match remaining pupdates against rules
    3.6 llm_triage           — Tier 2 LLM triage for unmatched pupdates
    4.  learning             — aggregate signals into learnings
    4.5 classification       — batch classify unclassified PR feedback
    5.  heartbeats           — nudge silent agents
    6.  projects             — auto-advance project phases
    7.  scheduled_skills     — run skills on their schedules
    8.  auto_investigate     — auto-run investigate skill on CI failures
    9.  morning_brief        — generate the daily morning brief
    10. brainstorm           — auto-brainstorm on Tue/Thu
"""

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Track cycle history for status reporting
_last_cycle = None
_cycle_count = 0

_status_cache = None
_status_cache_at = 0


# ---------------------------------------------------------------------------
# Phase functions — each returns its result dict (never raises)
# ---------------------------------------------------------------------------

def _phase_agents():
    """Phase 1: Process agent pupdates first (auto-complete tasks, etc.)."""
    from planet_maiko.agents.monitor import process_agent_pupdates
    return process_agent_pupdates()


def _phase_auto_complete_reviews():
    """Phase 1.5: Auto-complete review tasks when PRs are approved/merged."""
    try:
        from planet_maiko.models.pupdate import Pupdate
        from planet_maiko.models.task import Task
        from planet_maiko.database import db

        approved_prs = Pupdate.query.filter(
            Pupdate.type.in_(["pr_approved", "pr_merged"]),
            Pupdate.brain_processed == False,  # noqa: E712
        ).all()

        completed_count = 0
        for p in approved_prs:
            repo = (p.extra or {}).get("repo", "")
            pr_number = (p.extra or {}).get("number", "")
            if not (repo and pr_number):
                continue
            review_tasks = Task.query.filter(
                Task.type == "review",
                Task.status.in_(["new", "in_progress"]),
            ).all()
            for task in review_tasks:
                task_repo = (task.extra or {}).get("repo", "")
                task_number = str((task.extra or {}).get("number", ""))
                # Match by repo+number or by URL containing the PR
                if (task_repo == repo and task_number == str(pr_number)) or \
                   (task.url and f"/{pr_number}" in task.url and repo.split("/")[-1] in (task.url or "")):
                    task.status = "done"
                    completed_count += 1

        if completed_count:
            db.session.commit()
        return {"completed": completed_count}
    except Exception as e:
        logger.debug(f"[cycle] Review auto-complete skipped: {e}")
        return {"completed": 0}


def _phase_awareness():
    """Phase 2: Check for conflicts between active agents + attempt A2A resolution."""
    try:
        from planet_maiko.brain.awareness.conflicts import detect_conflicts, resolve_conflicts
        from planet_maiko.agents.coding_agent import list_prepared

        prepared = list_prepared()
        worktrees = [
            {"task_id": a.get("task_id", ""), "worktree_path": a.get("working_path", "")}
            for a in prepared if a.get("working_path")
        ]
        if len(worktrees) < 2:
            return {"conflicts": 0, "resolved": 0, "escalated": 0}

        conflicts = detect_conflicts(worktrees)
        if not conflicts:
            return {"conflicts": 0, "resolved": 0, "escalated": 0}

        resolution = resolve_conflicts(conflicts)
        return {"conflicts": len(conflicts), **resolution}
    except Exception as e:
        logger.debug(f"[cycle] Awareness check skipped: {e}")
        return {"conflicts": 0, "warnings_sent": 0}


def _phase_calendar_focus():
    """Phase 2.5: Auto-focus from calendar events."""
    try:
        from planet_maiko.brain.focus.manager import check_calendar_focus
        from planet_maiko.models.pupdate import Pupdate
        recent = Pupdate.query.filter(Pupdate.brain_processed == False).all()  # noqa: E712
        return {"changed": check_calendar_focus(recent)}
    except Exception as e:
        logger.debug(f"[cycle] Calendar focus check skipped: {e}")
        return {"changed": False}


def _phase_correlator():
    """Phase 3: Correlate related pupdates into incidents."""
    from planet_maiko.brain.pupdates.correlator import correlate
    return correlate()


def _phase_pupdates():
    """Phase 3.5: Process remaining pupdates through rules + triage."""
    from planet_maiko.brain.pupdates.processor import process as process_pupdates
    return process_pupdates()


def _phase_llm_triage():
    """Phase 3.6: Tier 2 LLM triage for unmatched pupdates."""
    try:
        from planet_maiko.config import load_config
        config = load_config()
        if not config.get("brain", {}).get("llm_triage", False):
            return {"processed": 0, "skipped": "disabled"}

        from planet_maiko.models.pupdate import Pupdate
        unmatched = Pupdate.query.filter(
            Pupdate.brain_processed == False,  # noqa: E712
            Pupdate.dismissed == False,  # noqa: E712
            Pupdate.read == False,  # noqa: E712
        ).limit(5).all()
        if not unmatched:
            return {"processed": 0}

        from planet_maiko.agents.brain_session import _get_runtime, triage_pupdate
        runtime = _get_runtime()
        if not (runtime and runtime.is_available()):
            return {"processed": 0, "skipped": "no_runtime"}

        from planet_maiko.database import db
        from planet_maiko.models.task import Task
        for p in unmatched:
            try:
                decision = triage_pupdate(p)
                if decision:
                    action = decision.get("action", "skip")
                    if action == "dismiss":
                        p.dismissed = True
                        p.dismissed_at = datetime.now(timezone.utc)
                    elif action == "mark_read":
                        p.read = True
                    elif action == "create_task":
                        task = Task(
                            id=f"task-{p.id}",
                            title=decision.get("task_title", p.title),
                            type=decision.get("task_type", "todo"),
                            priority=decision.get("task_priority", p.priority),
                            source_pupdate_id=p.id,
                            url=p.url,
                            tags=p.tags or [],
                        )
                        db.session.add(task)
                p.brain_processed = True
            except Exception as e:
                logger.warning(f"[cycle] LLM triage failed for {p.id}: {e}")
        db.session.commit()
        return {"processed": len(unmatched)}
    except Exception as e:
        logger.warning(f"[cycle] LLM triage phase error: {e}")
        return {"processed": 0, "error": str(e)}


def _phase_learning():
    """Phase 4: Aggregate feedback signals into learnings."""
    from planet_maiko.brain.learning.processor import process_signals
    return process_signals()


def _phase_classification():
    """Phase 4.5: Batch classify unclassified signals + pattern learnings."""
    try:
        from planet_maiko.brain.learning.classifier import (
            classify_unclassified_signals, classify_pattern_learnings
        )
        classified = classify_unclassified_signals(batch_size=20)
        reclassified = classify_pattern_learnings(batch_size=20)
        return {
            "classified_signals": classified,
            "classified_learnings": reclassified,
        }
    except Exception as e:
        logger.warning(f"[cycle] Classification error: {e}")
        return {"classified_signals": 0, "classified_learnings": 0, "error": str(e)}


def _phase_heartbeats():
    """Phase 5: Heartbeats — nudge silent agents."""
    try:
        from planet_maiko.agents.monitor import check_heartbeats
        return {"nudged": check_heartbeats()}
    except Exception as e:
        logger.warning(f"[cycle] Heartbeat check error: {e}")
        return {"nudged": 0, "error": str(e)}


def _phase_projects():
    """Phase 6: Project driver — auto-advance project phases."""
    try:
        from planet_maiko.brain.projects.driver import drive_projects
        return drive_projects()
    except Exception as e:
        logger.warning(f"[cycle] Project driver error: {e}")
        return {"advanced": 0, "completed": 0, "error": str(e)}


def _phase_scheduled_skills():
    """Phase 7: Run skills on their schedules."""
    try:
        from planet_maiko.pollers.skill_runner import run_scheduled_skills
        return run_scheduled_skills()
    except Exception as e:
        logger.warning(f"[cycle] Skill runner error: {e}")
        return []


def _phase_auto_investigate():
    """Phase 8: Auto-investigate CI failures and incidents."""
    try:
        from planet_maiko.models.pupdate import Pupdate
        investigate_types = ["pr_ci_failed", "incident", "error_spike"]
        to_investigate = Pupdate.query.filter(
            Pupdate.type.in_(investigate_types),
            Pupdate.brain_processed == True,  # noqa: E712
            Pupdate.dismissed == False,  # noqa: E712
            ~Pupdate.tags.contains("auto_investigated"),
        ).limit(2).all()

        investigated = 0
        if to_investigate:
            from planet_maiko.agents.brain_session import run_skill
            from planet_maiko.database import db
            for p in to_investigate:
                try:
                    run_skill("investigate", context={
                        "query": f"Investigate: {p.title}",
                        "context": f"Source: {p.source}\nURL: {p.url or ''}\n{p.body or ''}",
                        "pupdates": "[]", "tasks": "[]", "calendar": "[]",
                    })
                    p.tags = list(p.tags or []) + ["auto_investigated"]
                    investigated += 1
                except Exception as e:
                    logger.debug(f"[cycle] Auto-investigate of {p.id} failed: {e}")
            db.session.commit()
        return {"investigated": investigated}
    except Exception as e:
        logger.debug(f"[cycle] Auto-investigate skipped: {e}")
        return {"investigated": 0}


def _phase_morning_brief():
    """Phase 9: Generate the daily morning brief (first cycle of the day)."""
    try:
        from planet_maiko.models.skill_result import SkillResult
        today = datetime.now(timezone.utc).date()
        existing = SkillResult.query.filter(
            SkillResult.skill_name == "morning-brief",
            SkillResult.created_at >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
        ).first()
        if existing:
            return {"already_exists": True}

        from planet_maiko.agents.brain_session import run_skill
        result = run_skill("morning-brief", context={
            "pupdates": "[]", "tasks": "[]", "calendar": "[]",
        })
        if result and result.get("success"):
            from planet_maiko.database import db
            sr = SkillResult(
                skill_name="morning-brief",
                title=f"Morning Brief — {today.strftime('%B %d')}",
                content=result["output"],
            )
            db.session.add(sr)
            db.session.commit()
            return {"generated": True}
        return {"generated": False}
    except Exception as e:
        logger.debug(f"[cycle] Auto morning brief skipped: {e}")
        return {"generated": False, "error": str(e)}


def _phase_brainstorm():
    """Phase 10: Auto-brainstorm (Tuesdays and Thursdays)."""
    try:
        if datetime.now(timezone.utc).weekday() not in (1, 3):  # Tue, Thu
            return {"generated": False, "skipped": "wrong_day"}

        from planet_maiko.models.skill_result import SkillResult
        today = datetime.now(timezone.utc).date()
        existing = SkillResult.query.filter(
            SkillResult.skill_name == "brainstorm",
            SkillResult.created_at >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
        ).first()
        if existing:
            return {"already_exists": True}

        from planet_maiko.agents.brain_session import run_skill
        result = run_skill("brainstorm", context={
            "pupdates": "[]", "tasks": "[]", "calendar": "[]",
        })
        if result and result.get("success"):
            from planet_maiko.database import db
            sr = SkillResult(
                skill_name="brainstorm",
                title=f"Brainstorm — {today.strftime('%B %d')}",
                content=result["output"],
            )
            db.session.add(sr)
            db.session.commit()
            return {"generated": True}
        return {"generated": False}
    except Exception as e:
        logger.debug(f"[cycle] Auto brainstorm skipped: {e}")
        return {"generated": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# Phase order. Each entry is (result_key, phase_function). The orchestrator
# runs them in order, stores each result under its key, and fires plugin
# hooks for every phase.
_PHASES = [
    ("agents", _phase_agents),
    ("auto_complete_reviews", _phase_auto_complete_reviews),
    ("awareness", _phase_awareness),
    ("calendar_focus", _phase_calendar_focus),
    ("correlator", _phase_correlator),
    ("pupdates", _phase_pupdates),
    ("llm_triage", _phase_llm_triage),
    ("learning", _phase_learning),
    ("classification", _phase_classification),
    ("heartbeats", _phase_heartbeats),
    ("projects", _phase_projects),
    ("scheduled_skills", _phase_scheduled_skills),
    ("auto_investigate", _phase_auto_investigate),
    ("morning_brief", _phase_morning_brief),
    ("brainstorm", _phase_brainstorm),
]


def run(app):
    """Execute one full brain cycle.

    Args:
        app: Flask app (needed for app context)

    Returns:
        dict mapping phase name → result dict
    """
    global _last_cycle, _cycle_count

    with app.app_context():
        logger.info(f"=== Brain cycle #{_cycle_count + 1} ===")

        results = {}
        for key, phase_fn in _PHASES:
            results[key] = phase_fn()

        # Fire plugin hooks for all completed phases
        from planet_maiko.plugins.loader import fire_hook
        for phase_name, phase_results in results.items():
            fire_hook("on_brain_cycle", phase_name, phase_results, app)

        _last_cycle = datetime.now(timezone.utc)
        _cycle_count += 1

        logger.info(f"=== Cycle #{_cycle_count} complete ===")
        return results


def get_status():
    """Get brain status for the dashboard. Cached for 5 seconds."""
    global _status_cache, _status_cache_at
    if _status_cache and (time.time() - _status_cache_at) < 5:
        return _status_cache

    pending = {}
    try:
        from planet_maiko.models.pupdate import Pupdate
        from planet_maiko.models.signal import Signal
        from planet_maiko.models.learning import Learning
        pending["unprocessed_pupdates"] = Pupdate.query.filter_by(brain_processed=False, dismissed=False).count()
        pending["unclassified_signals"] = Signal.query.filter_by(category="pattern", aggregated=False).count()
        pending["pending_learnings"] = Learning.query.filter_by(status="pending").count()
    except Exception as e:
        logger.debug(f"[cycle] Status pending counts failed: {e}")

    _status_cache = {
        "last_cycle": _last_cycle.isoformat() if _last_cycle else None,
        "cycle_count": _cycle_count,
        "pending": pending,
    }
    _status_cache_at = time.time()
    return _status_cache
