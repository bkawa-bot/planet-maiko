"""Single entrypoint for resuming an agent's claude session.

Every path that spawns `claude --print --resume <session_id>` funnels
through wake_agent(). Without this, five independent daemon-thread
spawns race on the same session JSONL file — and Claude Code's session
format isn't safe for concurrent writers. Symptoms: the agent seemed
to "die in the background", or spun on itself after a nudge landed
while it was already working.

The orchestrator adds three things the direct spawns didn't:

  1. Per-task threading.Lock — no two claude processes ever run
     against the same session_id at once.
  2. Queue/drop policy per source — user-initiated messages wait
     their turn; redundant nudges are dropped silently.
  3. Agent.state bookkeeping (idle | working | stuck) so the UI can
     show a live indicator instead of guessing from last_active_at.

Headless kickoffs (new sessions) still go through
coding_agent._kickoff_agent_headless — those don't need the lock
because a brand-new session_id can't collide with itself.
"""

import logging
import os
import shutil
import subprocess
import threading
from collections import defaultdict
from datetime import datetime, timezone

from planet_maiko.database import db

logger = logging.getLogger(__name__)

# Queue the prompt and fire it on the next wake. Losing a user chat or
# review-iteration request is bad.
_QUEUEING_SOURCES = {"chat", "feedback", "review", "plan", "queued"}
# Drop silently — a second heartbeat while the agent is already working
# is noise.
_DROP_ON_BUSY_SOURCES = {"nudge", "heartbeat"}

_TASK_LOCKS_GUARD = threading.Lock()
_TASK_LOCKS = {}

_PENDING_GUARD = threading.Lock()
_PENDING_PROMPTS = defaultdict(list)


def _lock_for(task_id):
    with _TASK_LOCKS_GUARD:
        lock = _TASK_LOCKS.get(task_id)
        if lock is None:
            lock = threading.Lock()
            _TASK_LOCKS[task_id] = lock
        return lock


def is_working(task_id):
    """True when wake_agent is actively running claude for this task."""
    lock = _TASK_LOCKS.get(task_id)
    return lock is not None and lock.locked()


def claim_task(task_id):
    """Try to acquire the task's wake lock for a foreign caller (e.g.
    _kickoff_agent_headless). Returns the Lock object on success so
    the caller can release() it when their subprocess finishes;
    returns None if the lock is already held (a wake or prior kickoff
    is in flight).
    """
    lock = _lock_for(task_id)
    return lock if lock.acquire(blocking=False) else None


def wake_agent(task_id, prompt, source, working_path=None, session_id=None, app=None, extra_args=None):
    """Resume the agent's claude session.

    Args:
        task_id: the task whose agent should wake.
        prompt: initial input piped into claude --print.
        source: who asked for the wake. One of "chat", "feedback",
            "review", "plan", "nudge", "heartbeat", "queued".
        working_path: worktree path. Resolved from the session
            registry if omitted.
        session_id: session ID to resume. Resolved from the registry
            if omitted.
        app: Flask app object. Captured via current_app if omitted.
            Required for the state writeback; if unavailable the
            run still proceeds but the Agent.state field won't update.
        extra_args: extra CLI flags to append to the claude command.
            Used e.g. by plan-revise to re-enable --permission-mode plan
            for the resumed turn.

    Returns:
        (ok, mode) where mode is one of:
          "woke"    — a claude --resume subprocess was spawned.
          "queued"  — agent was already awake; prompt was appended
                      to the queue that drains on the next wake.
          "dropped" — agent was already awake; low-priority source
                      (nudge / heartbeat); no-op.
          "error"   — missing session_id, worktree, or claude CLI.
    """
    from flask import current_app
    if app is None:
        try:
            app = current_app._get_current_object()
        except RuntimeError:
            app = None

    if not session_id or not working_path:
        from planet_maiko.api.agents_api import _get_sessions
        info = _get_sessions().get(task_id) or {}
        session_id = session_id or info.get("session_id")
        working_path = working_path or info.get("working_path")

    if not session_id:
        logger.warning(f"[wake] no session for task {task_id} (source={source})")
        return False, "error"
    if not working_path or not os.path.isdir(working_path):
        logger.warning(f"[wake] no worktree for task {task_id} (source={source})")
        return False, "error"
    claude_path = shutil.which("claude")
    if not claude_path:
        logger.warning(f"[wake] claude CLI missing (task={task_id})")
        return False, "error"

    lock = _lock_for(task_id)
    if not lock.acquire(blocking=False):
        if source in _QUEUEING_SOURCES:
            with _PENDING_GUARD:
                _PENDING_PROMPTS[task_id].append(prompt)
                depth = len(_PENDING_PROMPTS[task_id])
            logger.info(f"[wake] {task_id} busy → queued (source={source}, depth={depth})")
            return True, "queued"
        logger.info(f"[wake] {task_id} busy → dropped (source={source})")
        return False, "dropped"

    def _run():
        try:
            set_agent_state(app, task_id, "working")
            # Drain any prompts that piled up before we grabbed the lock.
            with _PENDING_GUARD:
                queued = _PENDING_PROMPTS.pop(task_id, [])
            full_prompt = prompt
            if queued:
                full_prompt = (
                    prompt
                    + "\n\n--- Additional queued messages ---\n\n"
                    + "\n\n".join(queued)
                )

            cmd = [
                claude_path, "--print", "--output-format", "text",
                "--resume", session_id,
                "--dangerously-skip-permissions",
            ]
            if extra_args:
                cmd.extend(extra_args)
            log_path = os.path.join(working_path, "agent.log")
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(
                    f"\n\n# wake source={source} "
                    f"at={datetime.now(timezone.utc).isoformat()}\n\n"
                )
                log.flush()
                subprocess.run(
                    cmd,
                    input=full_prompt,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    cwd=working_path,
                )
        except Exception as e:
            logger.warning(f"[wake] run failed for {task_id}: {e}")
        finally:
            set_agent_state(app, task_id, "idle")
            lock.release()
            # Anything queued while we ran — fire a follow-up wake so
            # it doesn't sit until the next external trigger.
            with _PENDING_GUARD:
                leftover = _PENDING_PROMPTS.pop(task_id, [])
            if leftover:
                wake_agent(
                    task_id,
                    "\n\n".join(leftover),
                    source="queued",
                    working_path=working_path,
                    session_id=session_id,
                    app=app,
                )

    threading.Thread(target=_run, daemon=True, name=f"wake-{task_id}").start()
    return True, "woke"


def set_agent_state(app, task_id, state):
    """Best-effort state update. Silent on failure — this is
    UI/observability polish, not correctness-critical."""
    if app is None:
        return
    try:
        with app.app_context():
            from planet_maiko.models.task import Task
            from planet_maiko.models.agent_profile import AgentProfile
            task = db.session.get(Task, task_id)
            if not task or not task.assigned_agent_id:
                return
            profile = db.session.get(AgentProfile, task.assigned_agent_id)
            if not profile:
                return
            profile.state = state
            profile.last_active_at = datetime.now(timezone.utc)
            db.session.commit()
    except Exception as e:
        logger.debug(f"[wake] state write skipped for {task_id}: {e}")


# --- Cleanup --------------------------------------------------------------
#
# The session registry and Agent.state both accumulate stale entries:
# a task marked done still has its session_id on file, an agent that
# crashed mid-run still reads state="working". These helpers prune
# them on task completion + app startup + periodic ticks.

def prune_session(task_id):
    """Drop a task's session_id from the registry. Called when the
    task reaches a terminal status — further wakes on this task_id
    should fail fast rather than silently succeed on a dead worktree.

    The session JSONL on disk is kept (useful for LoRA harvest and
    post-mortems); only the task_id → session_id pointer goes away.
    """
    from planet_maiko.api.agents_api import _get_sessions, _save_sessions
    sessions = _get_sessions()
    if task_id in sessions:
        sessions.pop(task_id)
        _save_sessions()
        logger.info(f"[wake] pruned session for completed task {task_id}")


def validate_registry():
    """Walk the registry and drop entries whose task no longer exists
    or whose worktree is gone. Run once at app startup — otherwise
    the file grows forever.

    Must run inside an app_context (caller's responsibility). Rolls
    back the implicit read tx on exit so the session is clean for the
    next writer (reset_stale_working) — otherwise SQLAlchemy keeps the
    read tx open, each db.session.get extends it, and reset_stale_working
    piggybacks on a long-lived tx that triggers the slow-tx watcher
    (and, under concurrent startups, a real lock error).
    """
    from planet_maiko.api.agents_api import _get_sessions, _save_sessions
    from planet_maiko.models.task import Task

    sessions = _get_sessions()
    dropped = []
    try:
        for task_id, info in list(sessions.items()):
            task = db.session.get(Task, task_id)
            wp = info.get("working_path") if isinstance(info, dict) else None
            task_alive = task and task.status not in ("done", "cancelled")
            worktree_ok = bool(wp) and os.path.isdir(wp)
            if not task_alive or not worktree_ok:
                sessions.pop(task_id)
                dropped.append(task_id)
        if dropped:
            _save_sessions()
            logger.info(f"[wake] registry cleanup — dropped {len(dropped)} stale entries")
    finally:
        # Read-only from the DB's perspective (writes only touch the
        # JSON file). Rollback closes the implicit tx that db.session.get
        # opened so it doesn't linger into the next step.
        db.session.rollback()


def reset_stale_working():
    """Flip any Agent.state=='working' rows back to 'idle' on startup.
    Previous run crashed / was killed; the in-memory lock is gone so
    the working flag is meaningless until set_agent_state writes again.

    Bulk UPDATE rather than load-mutate-commit — holds the write lock
    for a single SQL statement instead of one per matching row. Matters
    on crowded startups where another process is still committing the
    migration tx; the previous load+iterate approach could block long
    enough to hit SQLite's busy_timeout.

    Must run inside an app_context.
    """
    from planet_maiko.models.agent_profile import AgentProfile
    count = (
        AgentProfile.query
        .filter_by(state="working")
        .update({"state": "idle"}, synchronize_session=False)
    )
    if count:
        db.session.commit()
        logger.info(f"[wake] startup — reset {count} stale working → idle")


# Agents that look busy but haven't produced a pupdate in this long
# are flagged stuck.
_STUCK_AFTER_MINUTES = 15


def check_stuck_agents(app):
    """Find agents whose state is 'working' but whose last_active_at
    timestamp is older than _STUCK_AFTER_MINUTES — the claude process
    probably crashed silently. Flip them to 'stuck' and emit an
    agent_stuck pupdate so the user can see it in Pack Requests.

    Idempotent: once flipped to stuck, the next tick won't re-emit.
    """
    if app is None:
        return 0
    from datetime import timedelta
    flagged = 0
    try:
        with app.app_context():
            from planet_maiko.models.agent_profile import AgentProfile
            from planet_maiko.models.task import Task
            from planet_maiko.models.pupdate import Pupdate
            import uuid as _uuid
            threshold = datetime.now(timezone.utc) - timedelta(minutes=_STUCK_AFTER_MINUTES)
            suspects = AgentProfile.query.filter(
                AgentProfile.state == "working",
                AgentProfile.last_active_at.isnot(None),
                AgentProfile.last_active_at < threshold,
            ).all()
            for p in suspects:
                # Double-check against the live lock: if the lock is
                # held, the claude process really IS running and
                # last_active_at just hasn't been bumped yet. Don't flag.
                tasks = Task.query.filter(
                    Task.assigned_agent_id == p.id,
                    Task.status == "in_progress",
                ).all()
                if any(is_working(t.id) for t in tasks):
                    continue
                p.state = "stuck"
                flagged += 1
                # Emit a single stuck pupdate per (agent, task)
                for t in tasks:
                    existing = Pupdate.query.filter(
                        Pupdate.type == "agent_stuck",
                        Pupdate.source_id == f"stuck/{t.id}",
                    ).first()
                    if existing:
                        continue
                    db.session.add(Pupdate(
                        id=f"stuck-{t.id}-{_uuid.uuid4().hex[:8]}",
                        source="maiko",
                        source_id=f"stuck/{t.id}",
                        type="agent_stuck",
                        priority="high",
                        title=f"{p.display_name} looks stuck on {t.title}",
                        body=(
                            f"No activity for {_STUCK_AFTER_MINUTES}+ minutes "
                            f"while state was 'working' — the claude process "
                            f"probably exited without reporting. Nudging them "
                            f"or clicking View Session should recover."
                        ),
                        tags=[p.id, t.id, "stuck"],
                        extra={"task_id": t.id, "agent_id": p.id},
                    ))
            if flagged:
                db.session.commit()
    except Exception as e:
        logger.warning(f"[wake] check_stuck_agents failed: {e}")
    return flagged


# Event listener — prune session when task status flips to terminal.
# Imported at module load (which happens via agents_api during app boot),
# so this fires for any Task.status mutation from then on.
try:
    from sqlalchemy import event
    from planet_maiko.models.task import Task as _Task

    @event.listens_for(_Task, "after_update")
    def _on_task_update(mapper, connection, target):
        if target.status in ("done", "cancelled"):
            try:
                prune_session(target.id)
            except Exception as e:
                logger.debug(f"[wake] prune_session skipped for {target.id}: {e}")
except Exception:
    # Import-order races shouldn't crash module load; the registry
    # will still get cleaned up on startup via validate_registry.
    pass
