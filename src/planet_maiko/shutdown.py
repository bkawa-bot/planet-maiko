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
from planet_maiko.models.signal import Signal
from planet_maiko.models.insight import Insight
from planet_maiko.models.learning import Learning
from planet_maiko.models.skill_result import SkillResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Age thresholds. Tuned so the default shutdown doesn't surprise with
# over-aggressive deletion. Exposed as module constants so tests or a
# future Settings UI can override.
# ---------------------------------------------------------------------------
PUPDATE_MAX_AGE_HOURS = 24
MESSAGE_MAX_AGE_DAYS = 14
SIGNAL_MAX_AGE_DAYS = 90
SKILL_RESULT_MAX_AGE_DAYS = 30
DISMISSED_MAX_AGE_DAYS = 30


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
        "skill_results": _count_prunable_skill_results(),
        "dismissed": _count_prunable_dismissed(),
    }


def _count_active_sessions():
    """Tasks with a live worktree and status that looks in-flight."""
    return Task.query.filter(
        Task.status.in_(("new", "in_progress", "in_review", "review")),
        # extra JSON contains working_path — SQLite doesn't have a great
        # JSON filter, so load + Python-filter. Cheap at Maiko scale.
    ).count()


def _count_done_worktrees():
    cnt = 0
    tasks = Task.query.filter(Task.status.in_(("done", "cancelled"))).all()
    for t in tasks:
        wp = (t.extra or {}).get("working_path")
        if wp and "/.maiko-worktrees/" in wp.replace("\\", "/"):
            cnt += 1
    return cnt


def _prunable_pupdates_query():
    action_list = list(ACTION_TYPES)
    cutoff = _strip_tz(_utc_now() - timedelta(hours=PUPDATE_MAX_AGE_HOURS))
    # Previously required Pupdate.read == True; the read flag has been
    # retired (there's no inbox). Any processed non-actionable pupdate
    # past its TTL is fair game.
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


def _prunable_skill_results_query():
    cutoff = _strip_tz(_utc_now() - timedelta(days=SKILL_RESULT_MAX_AGE_DAYS))
    return SkillResult.query.filter(SkillResult.created_at < cutoff)


def _count_prunable_skill_results():
    return _prunable_skill_results_query().count()


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
    """
    count = 0
    active = Task.query.filter(
        Task.status.in_(("new", "in_progress", "in_review", "review")),
    ).all()
    for t in active:
        wp = (t.extra or {}).get("working_path")
        if not wp:
            continue
        msg = AgentMessage(
            task_id=t.id,
            direction="to_agent",
            sender="maiko",
            message_type="shutdown",
            content=(
                "Maiko is settling the pack in for the night. If you're "
                "mid-task, wrap up the current step and exit cleanly; "
                "otherwise just rest. Your session and worktree will be "
                "here tomorrow if the user resumes."
            ),
        )
        db.session.add(msg)
        count += 1
    db.session.commit()
    return {"stopped": count}


def cleanup_worktrees():
    """Remove worktrees for tasks that are done / cancelled."""
    from planet_maiko.agents.coding_agent import cleanup_task_worktree

    cleaned = 0
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
    if cleaned:
        db.session.commit()

    # Also try to scrub .maiko-worktrees directories for repos that
    # have orphaned trees (task row gone entirely). Best-effort walk
    # over configured repo_roots.
    orphaned = 0
    try:
        from planet_maiko.config import load_config
        roots = (load_config().get("github") or {}).get("repo_roots") or []
        existing_paths = {
            (t.extra or {}).get("working_path") for t in Task.query.all()
            if (t.extra or {}).get("working_path")
        }
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
                    if leaf_path in existing_paths:
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


def prune_skill_results():
    q = _prunable_skill_results_query()
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
    "prune_skill_results": prune_skill_results,
    "prune_dismissed": prune_dismissed,
    "stop_server": stop_server,
}
