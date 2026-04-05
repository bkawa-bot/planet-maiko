"""Agent monitor - watches for agent pupdates and tracks agent activity.

Agents communicate back to Planet Maiko by creating pupdates with
source="agent". The monitor processes these to:

    - Track which agents are active (heartbeat detection)
    - Detect stuck agents (no pupdates for too long)
    - Auto-update task status when agents report completion
"""

import logging
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

# How long before an agent is considered idle
IDLE_THRESHOLD_MINUTES = 30


def get_agent_activity():
    """Get the latest activity for each agent.

    Returns:
        list of dicts with agent_id, last_message, last_seen, status
    """
    # Find all agent pupdates
    agent_pupdates = (
        Pupdate.query
        .filter_by(source="agent")
        .order_by(Pupdate.timestamp.desc())
        .all()
    )

    # Group by task_id tag to find per-agent activity
    agents = {}
    now = datetime.now(timezone.utc)

    for p in agent_pupdates:
        # Find the task_id from tags
        task_tags = [t for t in (p.tags or []) if t.startswith("task-")]
        agent_key = task_tags[0] if task_tags else p.id

        if agent_key not in agents:
            last_seen = p.timestamp
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            idle_minutes = (now - last_seen).total_seconds() / 60

            agents[agent_key] = {
                "task_id": agent_key,
                "last_message": p.title,
                "last_message_body": p.body,
                "last_seen": p.timestamp.isoformat(),
                "type": p.type,
                "pupdate_count": 0,
                "status": "idle" if idle_minutes > IDLE_THRESHOLD_MINUTES else "active",
                "idle_minutes": round(idle_minutes),
            }

        agents[agent_key]["pupdate_count"] += 1

    return list(agents.values())


def process_agent_pupdates():
    """Process unhandled agent pupdates.

    Looks for agent_done pupdates and auto-completes the linked task.

    Returns:
        dict with counts of actions taken
    """
    # Claim all unprocessed agent pupdates (mark brain_processed so
    # the pupdate processor's triage doesn't also handle them)
    agent_pupdates = (
        Pupdate.query
        .filter_by(source="agent", brain_processed=False)
        .all()
    )

    completed_tasks = 0

    for p in agent_pupdates:
        # Mark as processed so the pupdate processor skips these
        p.brain_processed = True
        p.read = True

        if p.type == "agent_done":
            task_tags = [t for t in (p.tags or []) if t.startswith("task-")]
            if task_tags:
                task = db.session.get(Task, task_tags[0])
                if task and task.status != "done":
                    task.status = "done"
                    task.updated_at = datetime.now(timezone.utc)
                    completed_tasks += 1
                    logger.info(f"[monitor] Agent completed task: {task.id}")

    if agent_pupdates:
        db.session.commit()

    return {"completed_tasks": completed_tasks, "processed": len(agent_pupdates)}


def get_stuck_agents():
    """Find agents that haven't reported in a while.

    Returns:
        list of agent activity dicts that are idle
    """
    activity = get_agent_activity()
    return [a for a in activity if a["status"] == "idle"]


def check_heartbeats():
    """Check for agents that haven't sent a pupdate recently. Send nudges."""
    from planet_maiko.models.agent_profile import AgentProfile

    threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

    # Find agents that are "working" but haven't sent a pupdate recently
    active_profiles = AgentProfile.query.filter(
        AgentProfile.last_active_at.isnot(None),
        AgentProfile.last_active_at < threshold,
        AgentProfile.breed != "completed",
    ).all()

    nudged = 0
    for profile in active_profiles:
        # Check if we already sent a nudge recently
        recent_nudge = Pupdate.query.filter(
            Pupdate.type == "agent_nudge",
            Pupdate.source_id == f"nudge/{profile.id}",
            Pupdate.timestamp > threshold,
        ).first()

        if not recent_nudge:
            nudge = Pupdate(
                id=f"nudge-{profile.id}-{int(datetime.now(timezone.utc).timestamp())}",
                source="maiko",
                source_id=f"nudge/{profile.id}",
                type="agent_nudge",
                priority="normal",
                title=f"Nudge: {profile.display_name} — are you still working?",
                body="No activity detected in 30+ minutes. Please report your status.",
                tags=[profile.id, "nudge"],
                extra={"agent_id": profile.id},
            )
            db.session.add(nudge)
            nudged += 1

    if nudged:
        db.session.commit()
    return nudged
