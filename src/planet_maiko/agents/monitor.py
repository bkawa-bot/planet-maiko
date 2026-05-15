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

    Stale rows are kept on the list so the user can see "Mochi.flow
    has been quiet for 9 days on the auth refactor" instead of the
    row silently vanishing.

    Filters out:
      - Tasks the agent finished (done / cancelled)
      - Tasks that have been deleted out from under the agent

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
    # idle/active/stale status). They include the post-tool-use hook,
    # which is the truest signal for "is this agent doing something
    # right now". They do NOT drive the speech-bubble text; the
    # displayed message comes from AgentMessage below (the actual chat).
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

        # Override the activity-derived status when the most recent
        # agent message means the agent is parked waiting for the user
        # to respond — visually distinct from active/idle/stale (still
        # working, just quiet) and from "ready" (FOLLOWUP_KINDS done
        # job, available for follow-up chat). The auto-nudge phase
        # uses the same signals to decide NOT to nudge a waiting agent.
        is_waiting_on_user = (
            (last.message_type or "") in ("stuck", "plan_for_approval")
            or (last.recipient or "").lower() == "user"
        )
        if is_waiting_on_user:
            a["status"] = "waiting"

    # Filter + enrich the agents dict for the active feed.
    #
    # `agents` is keyed by AgentJob.id (matches the agent's MAIKO_JOB_ID).
    # The parent Task, if any, is looked up afterward for display metadata.
    #
    # Visibility rules:
    #   - Job in queued / running / pending_approval: alive, keep.
    #   - FOLLOWUP_KINDS job in done with worktree still on disk:
    #     keep with status="ready" so the user sees "agent shipped, you
    #     can ask follow-ups" instead of the row vanishing the instant
    #     ready_for_review lands. Drops out once the worktree gets
    #     cleaned (shutdown ritual or pr_merged automation).
    #   - Anything else (terminal job + no resumable worktree): drop.
    #
    # Task fallback (no job, key is a Task.id): drop on done/cancelled.
    # Coding worktrees get cleaned on completion so wake_agent wouldn't
    # work anyway.
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.api.agent_outbox import FOLLOWUP_KINDS

    keep = {}
    for key, a in agents.items():
        # Job-first: the agent-side id IS the job.id.
        job = db.session.get(AgentJob, key)
        if job is not None:
            is_active = job.status in ("queued", "running", "pending_approval")
            is_ready_for_followup = (
                job.kind in FOLLOWUP_KINDS
                and job.status == "done"
                and bool(job.worktree_path)
            )
            if not (is_active or is_ready_for_followup):
                continue

            # Pull the linked Task (if any) for display metadata. The
            # task's title reads better than the job.title for linked
            # work; the job's metadata wins only when standalone.
            linked_task = (
                db.session.get(Task, job.source_task_id)
                if job.source_task_id else None
            )
            a["kind"] = "task" if linked_task is not None else "job"
            a["job_id"] = job.id
            # task_id stays the canonical inbox key (job.id) so chat /
            # wake routing keep working. Expose the real Task.id
            # separately for surfaces that need to call /tasks/<id>/...
            # endpoints (cancel, forget, route to /tasks/<id>/review).
            if linked_task is not None:
                a["linked_task_id"] = linked_task.id
            a["task_title"] = linked_task.title if linked_task else job.title
            a["task_status"] = linked_task.status if linked_task else job.status
            a["task_type"] = linked_task.type if linked_task else job.kind
            if is_ready_for_followup:
                # Override the pupdate-derived status so the UI shows
                # "ready" instead of stale active/idle from the last
                # heartbeat before completion.
                a["status"] = "ready"
            if job.agent_profile_id:
                profile = db.session.get(AgentProfile, job.agent_profile_id)
                if profile:
                    a["agent_name"] = profile.display_name
                    a["agent_id"] = profile.id
                    a["agent_avatar"] = profile.avatar
            keep[key] = a
            continue

        # Task fallback: key is a Task.id (tasks that never got an
        # AgentJob row). Drop on terminal status; coding worktrees
        # can't follow-up anyway.
        task = db.session.get(Task, a["task_id"])
        if task is None:
            continue
        if task.status in ("done", "cancelled"):
            continue
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

    # Surface running / queued AgentJobs that don't yet have any
    # agent pupdate or message. Without this, a freshly-queued job
    # is invisible until its agent emits the first heartbeat (or
    # never, if the kickoff fails). Keyed by job.id so a freshly-added
    # entry here gets superseded by the richer pupdate-driven row on
    # the next refresh once the agent emits its first heartbeat.
    pending_jobs = (
        AgentJob.query
        .filter(AgentJob.status.in_(["queued", "running"]))
        .all()
    )
    for job in pending_jobs:
        bucket_key = job.id
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

        # Pull the agent's latest from-agent message. The agent might
        # have emitted a "starting up" status before any pupdate
        # landed, and we want the bubble to show "I'm reading TASK.md"
        # rather than blank. Try both the bucket key (agents reply
        # with task_id=<job.id>) and the source task id.
        last = None
        last_keys = [job.id]
        if job.source_task_id and job.source_task_id != job.id:
            last_keys.append(job.source_task_id)
        for k in last_keys:
            cand = (
                AgentMessage.query
                .filter_by(task_id=k, direction="from_agent")
                .order_by(AgentMessage.created_at.desc())
                .first()
            )
            if cand is not None and (last is None or cand.created_at > last.created_at):
                last = cand
        last_seen = iso_utc(
            (last.created_at if last is not None else None)
            or job.started_at or job.created_at
        )
        last_msg_preview = None
        last_msg_body = None
        last_msg_type = None
        if last is not None:
            body = (last.content or "").strip()
            last_msg_preview = (
                body[:LAST_MESSAGE_PREVIEW_CHARS].rstrip() + "…"
                if len(body) > LAST_MESSAGE_PREVIEW_CHARS else body
            )
            last_msg_body = last.content
            last_msg_type = last.message_type

        entry = {
            "task_id": bucket_key,
            "kind": "task" if job.source_task_id else "job",
            "job_id": job.id,
            "last_message": last_msg_preview,
            "last_message_body": last_msg_body,
            "last_message_type": last_msg_type,
            "last_seen": last_seen,
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
                entry["agent_avatar"] = profile.avatar
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

    # Agents don't auto-close tasks. Only the user (via UI) or the
    # pr_merged automation (on PR landing) closes a coding task; review
    # and investigation jobs close their parent task via the
    # ready_for_review handler in brain_session.py. agent_done pupdates
    # are record-only signals; we mark them processed so the pupdate
    # processor doesn't re-handle them, but no task state changes.
    for p in agent_pupdates:
        p.brain_processed = True

    if agent_pupdates:
        db.session.commit()

    return {"completed_tasks": 0, "processed": len(agent_pupdates)}


def get_stuck_agents():
    """Find agents that haven't reported in a while.

    Returns:
        list of agent activity dicts whose status is idle or stale
        (i.e. anything not currently active).
    """
    activity = get_agent_activity()
    return [a for a in activity if a["status"] in ("idle", "stale")]


def get_recoverable_agents():
    """Find cancelled tasks + jobs whose worktree is still on disk.

    The active page shows these in a "Recently stopped" section with
    a revive button. The misclick recovery surface — without it, every
    accidental cancel is a day of context loss.

    Pulls from both Task and AgentJob:
      - Task in status="cancelled" with task.extra.working_path on disk
      - AgentJob in status="cancelled" with worktree_path on disk

    Linked jobs (job.source_task_id is set) are de-duplicated against
    the task entry — the surface row is keyed by the linked task.
    """
    import os
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.agent_job import AgentJob

    out = []
    seen_task_ids = set()

    cancelled_tasks = (
        Task.query
        .filter(Task.status == "cancelled")
        .order_by(Task.updated_at.desc())
        .all()
    )
    for t in cancelled_tasks:
        wp = (t.extra or {}).get("working_path")
        if not wp or not os.path.isdir(wp):
            continue
        entry = {
            "kind": "task",
            "task_id": t.id,
            "task_title": t.title,
            "task_status": t.status,
            "task_type": t.type,
            "stopped_at": iso_utc(t.updated_at),
            "working_path": wp,
        }
        if t.assigned_agent_id:
            profile = db.session.get(AgentProfile, t.assigned_agent_id)
            if profile:
                entry["agent_name"] = profile.display_name
                entry["agent_id"] = profile.id
        out.append(entry)
        seen_task_ids.add(t.id)

    cancelled_jobs = (
        AgentJob.query
        .filter(AgentJob.status == "cancelled")
        .filter(AgentJob.worktree_path.isnot(None))
        .order_by(AgentJob.finished_at.desc())
        .all()
    )
    for j in cancelled_jobs:
        if not j.worktree_path or not os.path.isdir(j.worktree_path):
            continue
        if j.source_task_id and j.source_task_id in seen_task_ids:
            continue  # represented by the task entry above
        entry = {
            "kind": "job",
            "task_id": j.id,  # frontend uses this for routing
            "job_id": j.id,
            "task_title": j.title,
            "task_status": j.status,
            "task_type": j.kind,
            "stopped_at": iso_utc(j.finished_at),
            "working_path": j.worktree_path,
        }
        if j.agent_profile_id:
            profile = db.session.get(AgentProfile, j.agent_profile_id)
            if profile:
                entry["agent_name"] = profile.display_name
                entry["agent_id"] = profile.id
        out.append(entry)

    return out


