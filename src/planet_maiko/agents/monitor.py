"""Agent monitor - watches for agent pupdates and tracks agent activity.

Agents communicate back to Planet Maiko by creating pupdates with
source="agent". The monitor processes these to:

    - Track which agents are active (heartbeat detection)
    - Detect stuck agents (no pupdates for too long)
    - Auto-update task status when agents report completion
"""

import logging
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db, iso_utc
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

# How long before an agent is considered idle (status=idle)
IDLE_THRESHOLD_MINUTES = 30
# How long before an agent is considered stale — surfaced separately
# so the user notices "this one's been quiet" instead of the row
# silently disappearing from the active feed.
STALE_THRESHOLD_DAYS = 7


def get_agent_activity():
    """Get the latest activity for each agent whose task is still open.

    Returns one dict per (agent, task) with a status of:
      - "active"  → reported within IDLE_THRESHOLD_MINUTES
      - "idle"    → quiet for >IDLE_THRESHOLD_MINUTES but <STALE_THRESHOLD_DAYS
      - "stale"   → quiet for >STALE_THRESHOLD_DAYS

    Stale rows used to be filtered out entirely; now they stay so the
    user can see "Mochi.flow has been quiet for 9 days on the auth
    refactor — was that supposed to wrap up?" instead of the row
    silently vanishing.

    Filters out:
      - Tasks the agent finished (done / cancelled)
      - Tasks that no longer exist (deleted out from under the agent)

    Returns:
        list of dicts with agent_id, last_message, last_seen, status
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile

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
    stale_cutoff = now - timedelta(days=STALE_THRESHOLD_DAYS)

    # Pupdates drive activity tracking (last_seen, pupdate_count,
    # idle/active/stale status) — they include the post-tool-use hook
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
            idle_minutes = (now - last_seen).total_seconds() / 60

            if last_seen < stale_cutoff:
                status = "stale"
            elif idle_minutes > IDLE_THRESHOLD_MINUTES:
                status = "idle"
            else:
                status = "active"

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
                "status": status,
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
    # needs the title to render one card per (agent, work-unit)
    # instead of one per agent — same agent profile working two
    # things should show two cards, distinguished by title).
    #
    # Standalone AgentJobs (cartograph / investigation / specialty
    # runs without a linked Task) get the same treatment. Their
    # _kickoff_agent_headless call passes task_id=<job_id>, so their
    # agent messages + pupdates land in the same per-id buckets the
    # task loop above already built — we just need a different
    # enrichment lookup since `Task.id == job_id` returns None.
    from planet_maiko.models.agent_job import AgentJob

    keep = {}
    for key, a in agents.items():
        task = db.session.get(Task, a["task_id"])
        if task is not None:
            if task.status in ("done", "cancelled"):
                continue  # agent's work on this one is over
            a["kind"] = "task"
            a["task_title"] = task.title
            a["task_status"] = task.status
            a["task_type"] = task.type
            if task.assigned_agent_id:
                profile = db.session.get(AgentProfile, task.assigned_agent_id)
                if profile:
                    a["agent_name"] = profile.display_name
                    a["agent_id"] = profile.id
            keep[key] = a
            continue

        # Not a task — maybe a standalone AgentJob (cartograph,
        # investigation, specialty skill run). Show it if it's still
        # active; jobs that finished / failed / cancelled don't belong
        # in the active feed.
        job = db.session.get(AgentJob, a["task_id"])
        if job is None:
            continue
        if job.source_task_id:
            # Linked-job — already represented by the task above.
            continue
        if job.status not in ("queued", "running"):
            continue
        a["kind"] = "job"
        a["job_id"] = job.id
        a["task_title"] = job.title
        a["task_status"] = job.status
        a["task_type"] = job.kind
        if job.agent_profile_id:
            profile = db.session.get(AgentProfile, job.agent_profile_id)
            if profile:
                a["agent_name"] = profile.display_name
                a["agent_id"] = profile.id
        keep[key] = a

    # Surface running / queued AgentJobs that don't yet have any
    # agent pupdate or message. Without this, a freshly-queued job
    # is invisible until its agent emits the first heartbeat (or
    # never, if the kickoff fails). For task-linked jobs we key on
    # the task_id (so the row merges with later pupdate-driven
    # entries) and for standalone jobs we key on job.id.
    pending_jobs = (
        AgentJob.query
        .filter(AgentJob.status.in_(["queued", "running"]))
        .all()
    )
    for job in pending_jobs:
        bucket_key = job.source_task_id or job.id
        if bucket_key in keep:
            # Already represented (either by pupdates above or by an
            # earlier loop iteration). Don't clobber its richer state.
            continue
        # Resolve title from linked task when present so the row reads
        # as the user-facing intent ("Review PR #42") rather than the
        # internal job kind.
        linked = (
            db.session.get(Task, job.source_task_id)
            if job.source_task_id else None
        )
        if linked is not None and linked.status in ("done", "cancelled"):
            continue  # task closed out — don't keep its job around in active
        entry = {
            "task_id": bucket_key,
            "kind": "job",
            "job_id": job.id,
            "last_message": None,
            "last_message_body": None,
            "last_message_type": None,
            "last_seen": iso_utc(job.started_at or job.created_at),
            "type": job.kind,
            "pupdate_count": 0,
            "status": "active" if job.status == "running" else "idle",
            "idle_minutes": 0,
            "task_title": (linked.title if linked else job.title),
            "task_status": job.status,
            "task_type": job.kind,
        }
        if job.agent_profile_id:
            profile = db.session.get(AgentProfile, job.agent_profile_id)
            if profile:
                entry["agent_name"] = profile.display_name
                entry["agent_id"] = profile.id
        else:
            # Queued job that hasn't been picked up by the executor
            # yet (so no agent_profile_id assigned). Without an
            # explicit agent_name the frontend falls back to the raw
            # bucket key, which on a job-only row reads as "job-abc…".
            entry["agent_name"] = f"Spawning {job.kind} agent…"
        keep[bucket_key] = entry

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
        list of agent activity dicts whose status is idle or stale
        (i.e. anything not currently active).
    """
    activity = get_agent_activity()
    return [a for a in activity if a["status"] in ("idle", "stale")]


