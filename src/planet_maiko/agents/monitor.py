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

    # source="agent" catches the post-tool-use hook (agent_update). The
    # reply-handler pupdates (ready_for_review, stuck, plan_for_approval,
    # agent_message) are stamped source="maiko" but represent agent
    # activity just the same — their task_id tag drives the same
    # per-agent grouping. Without them, a review agent that posts
    # ready_for_review without any intermediate tool-use hook looks
    # "dormant" on the Agents page even though it's clearly finished.
    from sqlalchemy import or_, and_
    agent_pupdates = (
        Pupdate.query
        .filter(
            or_(
                Pupdate.source == "agent",
                and_(Pupdate.source == "maiko", Pupdate.type.like("agent_%")),
            )
        )
        .order_by(Pupdate.timestamp.desc())
        .all()
    )

    agents = {}
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=STALE_AGENT_DAYS)

    # Pupdates drive activity tracking (last_seen, pupdate_count,
    # idle/active status) — they include the post-tool-use hook
    # which is the truest signal for "is this agent doing something
    # right now". They do NOT drive the speech-bubble text; that
    # used to read pupdate titles like "Agent ready: <task>" and
    # filter out post-tool-use noise, but the result was a stale
    # bubble parroting prepare-time state long after the agent had
    # said real things via reply()/maiko report. The displayed
    # message comes from AgentMessage below — the actual chat.
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
                # Filled in below from the AgentMessage table — the
                # canonical source for "what the agent said".
                "last_message": None,
                "last_message_body": None,
                "last_message_type": None,
                "last_seen": p.timestamp.isoformat(),
                "type": p.type,
                "pupdate_count": 0,
                "status": "idle" if idle_minutes > IDLE_THRESHOLD_MINUTES else "active",
                "idle_minutes": round(idle_minutes),
            }

        a = agents[agent_key]
        a["pupdate_count"] += 1

    # Override the displayed last_message from the AgentMessage table.
    # The speech bubble is conceptually "what the agent last said in
    # chat" — the agent_messages table holds those (every reply()
    # MCP call lands here, plus user messages going the other
    # direction). Pupdates carry state-transition titles ("Agent
    # ready: ...", "Mochi replied: ...") that aren't always what
    # the user wants to see in the bubble.
    LAST_MESSAGE_PREVIEW_CHARS = 140
    from planet_maiko.models.agent_message import AgentMessage
    for agent_key, a in agents.items():
        last = (
            AgentMessage.query
            .filter_by(task_id=agent_key, direction="from_agent")
            .order_by(AgentMessage.created_at.desc())
            .first()
        )
        if last is None:
            continue
        body = (last.content or "").strip()
        if len(body) > LAST_MESSAGE_PREVIEW_CHARS:
            preview = body[:LAST_MESSAGE_PREVIEW_CHARS].rstrip() + "…"
        else:
            preview = body
        a["last_message"] = preview
        a["last_message_body"] = last.content
        a["last_message_type"] = last.message_type

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
    """Auto-wake silent agents on their in-progress tasks.

    Previously also emitted user-facing `agent_nudge` pupdates, but those
    were noise now that wake_agent handles the actual re-wake. The
    circuit-breaker in wake_agent drops source="heartbeat" calls when
    the agent is already running, so this is safe to fire every cycle —
    it only actually wakes truly silent agents.
    """
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.task import Task
    from planet_maiko.agents.wake import wake_agent, check_stuck_agents

    # Flag silently-crashed agents first (state=working but no progress) —
    # this also clears the "working" flag so the auto-wake below can
    # actually fire on them.
    try:
        from flask import current_app
        check_stuck_agents(current_app._get_current_object())
    except Exception:
        pass

    threshold = datetime.now(timezone.utc) - timedelta(minutes=30)

    active_profiles = AgentProfile.query.filter(
        AgentProfile.last_active_at.isnot(None),
        AgentProfile.last_active_at < threshold,
        (AgentProfile.archived == False) | (AgentProfile.archived == None),  # noqa: E712
    ).all()

    woken = 0
    for profile in active_profiles:
        tasks = Task.query.filter(
            Task.assigned_agent_id == profile.id,
            Task.status == "in_progress",
        ).all()
        for t in tasks:
            wp = (t.extra or {}).get("working_path")
            if not wp:
                continue
            ok, _mode = wake_agent(
                t.id,
                "Heartbeat check — call check_inbox, post a quick status via "
                "reply(message_type='status'), and continue if you still have work.",
                source="heartbeat",
                working_path=wp,
            )
            if ok:
                woken += 1

    return woken
