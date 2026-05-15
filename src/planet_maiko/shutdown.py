"""Shutdown / cleanup routines — "Maiko settles in for the night".

Each function here corresponds to one narrator line in the UI, and
returns a dict describing what it did. Nothing here touches identity
records (tasks, profiles, projects, active learnings/insights, config)
— shutdown only prunes noise, never history the user might want.

Safety posture:
    - Pre-computed preview counts mirror the real query, so the user
      sees exactly what each step would touch before committing.
    - Deletions are scoped by `read + processed + category != action`
      or `dismissed / aggregated / incorporated_at` flags so in-flight
      data stays put.
    - stop_server defers the SIGTERM by 500ms so the HTTP response
      reaches the browser before the process exits.
"""

import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate, ACTION_TYPES
from planet_maiko.models.task import Task
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.models.agent_job import AgentJob
from planet_maiko.models.signal import Signal
from planet_maiko.models.insight import Insight
from planet_maiko.models.learning import Learning

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Age thresholds. Tuned so the default shutdown doesn't surprise with
# over-aggressive deletion. Exposed as module constants so tests or a
# future Settings UI can override.
# ---------------------------------------------------------------------------
PUPDATE_MAX_AGE_HOURS = 24
MESSAGE_MAX_AGE_DAYS = 14
SIGNAL_MAX_AGE_DAYS = 90
DISMISSED_MAX_AGE_DAYS = 30
# Follow-up-capable AgentJobs (review / investigation / cartograph etc.)
# keep their worktree post-completion so the user can ask the agent
# follow-up questions via wake_agent. Without this gate the shutdown
# ritual would nuke yesterday's investigation worktree, breaking
# tomorrow's "let me ask one more thing" flow. Past this many days the
# follow-up window is closed enough that the worktree is fair game.
# Picked shorter than MESSAGE_MAX_AGE_DAYS (14) so we don't keep
# worktrees alive past when their inbox messages get pruned anyway.
AGENT_JOB_FOLLOWUP_MAX_AGE_DAYS = 7


def _utc_now():
    return datetime.now(timezone.utc)


def _strip_tz(dt):
    """SQLite returns naive datetimes; comparisons need naive too."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# Preview — pre-count each step so the modal can show the user what
# they're about to do.
# ---------------------------------------------------------------------------

def preview():
    """Count what each cleanup step would touch. No writes."""
    return {
        "active_sessions": _count_active_sessions(),
        "worktrees": _count_done_worktrees(),
        "pupdates": _count_prunable_pupdates(),
        "agent_messages": _count_prunable_messages(),
        "signals": _count_prunable_signals(),
        "dismissed": _count_prunable_dismissed(),
    }


def _count_active_sessions():
    """Tasks + AgentJobs with live worktrees — union so a pack-owned
    run without a linked Task still counts for the "N active" banner
    the shutdown modal shows before you commit."""
    task_cnt = Task.query.filter(
        Task.status.in_(("new", "in_progress", "in_review", "review")),
    ).count()
    job_cnt = AgentJob.query.filter(
        AgentJob.status.in_(("queued", "running", "pending_approval")),
    ).count()
    return task_cnt + job_cnt


def _job_worktree_is_cleanable(job, cutoff):
    """True when the job's worktree should be cleaned in this pass.

    Coding (and any other non-follow-up kind) is always eligible the
    moment it finishes. Follow-up kinds (review / investigation /
    cartograph) only become eligible past the age cutoff so the user
    can still wake them for follow-up questions in the meantime.
    finished_at unset means we can't reason about age — leave it.
    """
    from planet_maiko.api.agent_outbox import FOLLOWUP_KINDS
    if job.kind not in FOLLOWUP_KINDS:
        return True
    if job.finished_at is None:
        return False
    return _strip_tz(job.finished_at) < cutoff


def _count_done_worktrees():
    cnt = 0
    tasks = Task.query.filter(Task.status.in_(("done", "cancelled"))).all()
    for t in tasks:
        wp = (t.extra or {}).get("working_path")
        if wp and "/.maiko-worktrees/" in wp.replace("\\", "/"):
            cnt += 1
    # AgentJobs that finished (done / failed / cancelled) still own
    # their worktree until shutdown cleans it up. Follow-up kinds are
    # gated by age so fresh investigations / reviews / cartograph walks
    # survive the ritual and the user can still ask follow-ups.
    cutoff = _strip_tz(_utc_now() - timedelta(days=AGENT_JOB_FOLLOWUP_MAX_AGE_DAYS))
    done_jobs = AgentJob.query.filter(
        AgentJob.status.in_(("done", "failed", "cancelled")),
        AgentJob.worktree_path.isnot(None),
    ).all()
    for j in done_jobs:
        wp = j.worktree_path or ""
        if not (wp and "/.maiko-worktrees/" in wp.replace("\\", "/")):
            continue
        if not _job_worktree_is_cleanable(j, cutoff):
            continue
        cnt += 1
    return cnt


def _prunable_pupdates_query():
    action_list = list(ACTION_TYPES)
    cutoff = _strip_tz(_utc_now() - timedelta(hours=PUPDATE_MAX_AGE_HOURS))
    # Any processed non-actionable pupdate past its TTL is fair game.
    return Pupdate.query.filter(
        Pupdate.brain_processed.is_(True),
        ~Pupdate.type.in_(action_list),
        Pupdate.timestamp < cutoff,
    )


def _count_prunable_pupdates():
    return _prunable_pupdates_query().count()


def _prunable_messages_query():
    cutoff = _strip_tz(_utc_now() - timedelta(days=MESSAGE_MAX_AGE_DAYS))
    done_task_ids = [
        t.id for t in Task.query.filter(Task.status.in_(("done", "cancelled"))).with_entities(Task.id).all()
    ]
    if not done_task_ids:
        return AgentMessage.query.filter(False)
    return AgentMessage.query.filter(
        AgentMessage.task_id.in_(done_task_ids),
        AgentMessage.created_at < cutoff,
    )


def _count_prunable_messages():
    return _prunable_messages_query().count()


def _prunable_signals_query():
    cutoff = _strip_tz(_utc_now() - timedelta(days=SIGNAL_MAX_AGE_DAYS))
    return Signal.query.filter(
        Signal.incorporated_at.isnot(None),
        Signal.incorporated_at < cutoff,
    )


def _count_prunable_signals():
    return _prunable_signals_query().count()


def _count_prunable_dismissed():
    cutoff = _strip_tz(_utc_now() - timedelta(days=DISMISSED_MAX_AGE_DAYS))
    ins = Insight.query.filter(Insight.status == "dismissed", Insight.updated_at < cutoff).count()
    le = Learning.query.filter(Learning.status == "dismissed", Learning.updated_at < cutoff).count()
    pu = Pupdate.query.filter(
        Pupdate.dismissed.is_(True),
        Pupdate.dismissed_at.isnot(None),
        Pupdate.dismissed_at < cutoff,
    ).count()
    return ins + le + pu


# ---------------------------------------------------------------------------
# Step runners — each returns a dict the UI renders as the "done" line.
# ---------------------------------------------------------------------------

def stop_active_agents():
    """Send a stop signal to every in-flight agent session.

    We can't reach inside the Claude subprocess (headless agents run as
    fire-and-forget daemon threads, no PID tracking), but we can queue
    an agent-inbox message that each agent reads on its next Stop-hook
    wake. The message tells them the pack is calling it a night — if
    they're mid-work they finish gracefully, if dormant they simply
    don't wake back up. Session IDs stay in agent-sessions.json so a
    fresh "Resume" tomorrow still works.

    Pack-owned AgentJob runs (cartograph, investigation, review)
    report to MCP with task_id == job.id, so we also need to drop
    shutdown messages on AgentJob ids for the outbox handler to
    route them correctly.
    """
    body = (
        "Maiko is settling the pack in for the night. If you're "
        "mid-task, wrap up the current step and exit cleanly; "
        "otherwise just rest. Your session and worktree will be "
        "here tomorrow if the user resumes."
    )

    count = 0
    active = Task.query.filter(
        Task.status.in_(("new", "in_progress", "in_review", "review")),
    ).all()
    for t in active:
        wp = (t.extra or {}).get("working_path")
        if not wp:
            continue
        db.session.add(AgentMessage(
            task_id=t.id,
            direction="to_agent",
            sender="maiko",
            message_type="shutdown",
            content=body,
        ))
        count += 1

    active_jobs = AgentJob.query.filter(
        AgentJob.status.in_(("queued", "running")),
        AgentJob.worktree_path.isnot(None),
    ).all()
    for j in active_jobs:
        db.session.add(AgentMessage(
            task_id=j.id,
            direction="to_agent",
            sender="maiko",
            message_type="shutdown",
            content=body,
        ))
        count += 1

    db.session.commit()
    return {"stopped": count}


def cleanup_worktrees():
    """Remove worktrees for tasks + AgentJobs that are terminally done.

    Pack-owned worktrees live on AgentJob.worktree_path (not on a
    Task). Both paths matter: we clean up worktrees for done/cancelled
    Tasks AND done/failed/cancelled AgentJobs, and the orphan-directory
    sweep treats **active** AgentJob paths as protected so it doesn't
    nuke a cartograph or investigation mid-run.
    """
    from planet_maiko.agents.runtime import cleanup_task_worktree, cleanup as _cleanup_worktree_paths

    cleaned = 0

    # Task-owned worktrees (coding tasks mostly).
    tasks = Task.query.filter(Task.status.in_(("done", "cancelled"))).all()
    for t in tasks:
        wp = (t.extra or {}).get("working_path")
        if not wp or "/.maiko-worktrees/" not in wp.replace("\\", "/"):
            continue
        try:
            cleanup_task_worktree(t)
            cleaned += 1
            # Clear the now-dead paths from extra so future polls don't
            # keep offering "Review diff" links into nowhere.
            extra = dict(t.extra or {})
            extra.pop("working_path", None)
            extra.pop("branch", None)
            t.extra = extra
        except Exception as e:
            logger.warning(f"[shutdown] Worktree cleanup failed for {t.id}: {e}")

    # AgentJob-owned worktrees (cartograph / investigation / review
    # runs that finished). Don't touch queued/running/pending_approval —
    # those still need their worktree. Follow-up kinds are age-gated:
    # fresh ones survive the ritual so the user can wake them tomorrow
    # to ask "what about X?", but past AGENT_JOB_FOLLOWUP_MAX_AGE_DAYS
    # the worktree is fair game (worth more as disk space than as a
    # follow-up surface that the user has clearly moved on from).
    cutoff = _strip_tz(_utc_now() - timedelta(days=AGENT_JOB_FOLLOWUP_MAX_AGE_DAYS))
    done_jobs = AgentJob.query.filter(
        AgentJob.status.in_(("done", "failed", "cancelled")),
        AgentJob.worktree_path.isnot(None),
    ).all()
    for j in done_jobs:
        wp = j.worktree_path or ""
        if "/.maiko-worktrees/" not in wp.replace("\\", "/"):
            continue
        if not _job_worktree_is_cleanable(j, cutoff):
            continue
        try:
            _cleanup_worktree_paths(j.worktree_path, j.branch)
            cleaned += 1
            j.worktree_path = None
            j.branch = None
        except Exception as e:
            logger.warning(f"[shutdown] AgentJob worktree cleanup failed for {j.id}: {e}")

    if cleaned:
        db.session.commit()

    # Orphan sweep — scrub .maiko-worktrees directories on disk that no
    # DB row claims. CRITICAL: the "known paths" set must union Tasks
    # AND AgentJobs, otherwise we'd delete the worktree of an active
    # pack-owned run (the Task row is null, only AgentJob.worktree_path
    # points at it).
    orphaned = 0
    try:
        from planet_maiko.config import load_config
        roots = (load_config().get("github") or {}).get("repo_roots") or []
        known_paths = set()
        for t in Task.query.all():
            wp = (t.extra or {}).get("working_path")
            if wp:
                known_paths.add(wp)
        for j in AgentJob.query.filter(AgentJob.worktree_path.isnot(None)).all():
            if j.worktree_path:
                known_paths.add(j.worktree_path)
        for root in roots:
            root_exp = os.path.expanduser(root)
            if not os.path.isdir(root_exp):
                continue
            for repo_name in os.listdir(root_exp):
                wt_dir = os.path.join(root_exp, repo_name, ".maiko-worktrees")
                if not os.path.isdir(wt_dir):
                    continue
                for leaf in os.listdir(wt_dir):
                    leaf_path = os.path.join(wt_dir, leaf)
                    if leaf_path in known_paths:
                        continue
                    try:
                        shutil.rmtree(leaf_path, ignore_errors=True)
                        orphaned += 1
                    except Exception:
                        pass
    except Exception as e:
        logger.debug(f"[shutdown] Orphan worktree sweep failed: {e}")

    return {"cleaned": cleaned, "orphaned": orphaned}


def prune_pupdates():
    q = _prunable_pupdates_query()
    count = q.count()
    q.delete(synchronize_session=False)
    db.session.commit()
    return {"deleted": count}


def prune_messages():
    q = _prunable_messages_query()
    count = q.count()
    q.delete(synchronize_session=False)
    db.session.commit()
    return {"deleted": count}


def prune_signals():
    q = _prunable_signals_query()
    count = q.count()
    q.delete(synchronize_session=False)
    db.session.commit()
    return {"deleted": count}


def prune_dismissed():
    cutoff = _strip_tz(_utc_now() - timedelta(days=DISMISSED_MAX_AGE_DAYS))
    ins_q = Insight.query.filter(Insight.status == "dismissed", Insight.updated_at < cutoff)
    le_q = Learning.query.filter(Learning.status == "dismissed", Learning.updated_at < cutoff)
    pu_q = Pupdate.query.filter(
        Pupdate.dismissed.is_(True),
        Pupdate.dismissed_at.isnot(None),
        Pupdate.dismissed_at < cutoff,
    )
    ins_n, le_n, pu_n = ins_q.count(), le_q.count(), pu_q.count()
    ins_q.delete(synchronize_session=False)
    le_q.delete(synchronize_session=False)
    pu_q.delete(synchronize_session=False)
    db.session.commit()
    return {"insights": ins_n, "learnings": le_n, "pupdates": pu_n, "deleted": ins_n + le_n + pu_n}


def stop_server():
    """Schedule a graceful shutdown after the response is sent.

    Flask needs to return before the process dies, otherwise the user
    sees a connection-refused error instead of the "goodnight" line.
    0.5s delay is plenty for the JSON response to fly.

    Stops the background scheduler first so in-flight brain cycles
    don't fire mid-teardown. Mirrors brain_api:/system/shutdown — the
    two endpoints share the same kill path; this one just narrates.
    """
    try:
        from flask import current_app
        scheduler = current_app.config.get("SCHEDULER")
        if scheduler:
            scheduler.stop()
    except Exception as e:
        logger.debug(f"[shutdown] scheduler stop skipped: {e}")

    def _delayed_exit():
        time.sleep(0.5)
        # os._exit bypasses Python's atexit hooks, which is actually what
        # we want for a user-initiated shutdown — SQLite WAL is already
        # flushed by each db.session.commit, and Claude subprocess threads
        # are daemonized so they die with the parent.
        os._exit(0)

    threading.Thread(target=_delayed_exit, daemon=True, name="shutdown-exit").start()
    return {"scheduled": True}


# Dispatch table for the API — keeps the route thin.
STEPS = {
    "stop_agents": stop_active_agents,
    "cleanup_worktrees": cleanup_worktrees,
    "prune_pupdates": prune_pupdates,
    "prune_messages": prune_messages,
    "prune_signals": prune_signals,
    "prune_dismissed": prune_dismissed,
    "stop_server": stop_server,
}
