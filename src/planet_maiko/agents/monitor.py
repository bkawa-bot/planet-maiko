"""Agent monitor - watches for agent pupdates and tracks agent activity.

Agents communicate back to Planet Maiko by creating pupdates with
source="agent". The monitor processes these to:

    - Track which agents are active (heartbeat detection)
    - Detect stuck agents (no pupdates for too long)
    - Auto-update task status when agents report completion
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

# How long before an agent is considered idle
IDLE_THRESHOLD_MINUTES = 30


def get_agent_activity():
    """Get the latest activity for each agent whose task is still open.

    Filters out:
      - Tasks the agent finished (done / cancelled)
      - Tasks that no longer exist (deleted out from under the agent)
      - Tasks whose most recent agent pupdate is older than
        STALE_AGENT_DAYS — the agent's clearly abandoned this one,
        nothing productive happens from surfacing it in "Active"

    Returns:
        list of dicts with agent_id, last_message, last_seen, status
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile

    STALE_AGENT_DAYS = 7

    agent_pupdates = (
        Pupdate.query
        .filter_by(source="agent")
        .order_by(Pupdate.timestamp.desc())
        .all()
    )

    agents = {}
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=STALE_AGENT_DAYS)

    for p in agent_pupdates:
        agent_key = (p.tags or [None])[0] or p.id

        if agent_key not in agents:
            last_seen = p.timestamp
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            if last_seen < stale_cutoff:
                continue  # Abandoned task, skip entirely
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

        if agent_key in agents:
            agents[agent_key]["pupdate_count"] += 1

    # Drop tasks that are finished or no longer exist, and enrich
    # the rest with agent profile info + the task's title (the UI
    # needs the title to render one card per (agent, task) instead of
    # one per agent — same agent profile working two tasks should
    # show two cards, distinguished by task title).
    keep = {}
    for key, a in agents.items():
        task = db.session.get(Task, a["task_id"])
        if not task:
            continue  # task was deleted out from under the agent
        if task.status in ("done", "cancelled"):
            continue  # agent's work on this one is over
        a["task_title"] = task.title
        a["task_status"] = task.status
        a["task_type"] = task.type
        if task.assigned_agent_id:
            profile = db.session.get(AgentProfile, task.assigned_agent_id)
            if profile:
                a["agent_name"] = profile.display_name
                a["agent_id"] = profile.id
        keep[key] = a

    return list(keep.values())


def get_queued_agent_tasks():
    """Tasks that have an assigned agent but haven't started yet.

    "Haven't started" means the cycle's execute phase hasn't prepared
    a worktree for it (no working_path on task.extra) and there are no
    agent pupdates yet. These are the tasks the user assigned (or that
    the cycle routed) but that are still waiting for the next cycle
    tick to fire — without surfacing them, the AgentsActiveTab looks
    empty and the user thinks "did the review actually start?"

    Returns:
        list of dicts with task_id, title, type, agent_id, agent_name,
        assigned_at, queued_for_minutes.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile

    candidates = Task.query.filter(
        Task.status.in_(["new", "blocked"]),
        Task.assigned_agent_id.isnot(None),
    ).all()

    now = datetime.now(timezone.utc)
    out = []
    for t in candidates:
        meta = t.extra or {}
        if meta.get("working_path"):
            continue  # worktree already prepared — covered by /agents
        # Skip tasks that already have agent pupdates (covered by activity).
        has_activity = (
            Pupdate.query.filter(
                Pupdate.source == "agent",
                Pupdate.tags.contains(t.id),
            ).first() is not None
        )
        if has_activity:
            continue

        agent_name = t.assigned_agent_id or "?"
        if t.assigned_agent_id:
            profile = db.session.get(AgentProfile, t.assigned_agent_id)
            if profile:
                agent_name = profile.display_name

        updated = t.updated_at or t.created_at
        if updated and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        queued_minutes = round((now - updated).total_seconds() / 60) if updated else 0

        out.append({
            "task_id": t.id,
            "title": t.title,
            "type": t.type,
            "agent_id": t.assigned_agent_id,
            "agent_name": agent_name,
            "queued_for_minutes": queued_minutes,
            "url": t.url,
        })

    out.sort(key=lambda x: x["queued_for_minutes"], reverse=True)
    return out


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
        (AgentProfile.archived == False) | (AgentProfile.archived == None),  # noqa: E712
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
                id=f"nudge-{profile.id}-{uuid.uuid4().hex[:8]}",
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
