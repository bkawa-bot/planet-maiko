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


def wake_agent(task_id, prompt, source, working_path=None, session_id=None, app=None):
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
