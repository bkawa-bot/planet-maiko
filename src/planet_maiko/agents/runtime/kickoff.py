"""Headless kickoff for any agent role — fires `claude --print` in a
detached subprocess so the agent runs autonomously without a terminal.
Talks to MCP via the channel server registered in .mcp.json."""

import logging
import os
import re
import subprocess
import threading
import time as _time
import uuid

from planet_maiko.agents.runtime.process import (
    register_running_process,
    unregister_running_process,
)

logger = logging.getLogger(__name__)


_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
# Path validation: catch shell metacharacters but allow real path chars
# (letters, digits, spaces, dot, dash, underscore, slash, backslash, colon, paren).
_UNSAFE_PATH_CHARS = re.compile(r'[;&|`$<>!"*?\n\r]')


def _tail_log(path, max_chars=400):
    """Read the last ~max_chars of a log file. Used to capture a useful
    excerpt of why claude exited so the AgentJob row carries context
    instead of a bare exit code."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        snippet = data[-max_chars:].strip()
        # Strip blank leading lines so the tail reads clean.
        return snippet or "(empty log)"
    except Exception as e:
        return f"(could not read log: {e})"


def _mark_kickoff_failed(app, kickoff_id, error):
    """Mark the AgentJob (or Task) tied to this kickoff as failed.

    `kickoff_id` is what the caller passed as task_id — for the
    unified path it's an AgentJob.id (`job-...`). For older Task-keyed
    runs it's a Task.id. We try AgentJob first since that's the
    canonical post-unification target.
    """
    if app is None:
        return
    try:
        with app.app_context():
            from planet_maiko.database import db
            from planet_maiko.models.agent_job import AgentJob
            from planet_maiko.models.task import Task
            from datetime import datetime, timezone

            job = db.session.get(AgentJob, kickoff_id)
            if job and job.status in ("queued", "running"):
                job.status = "failed"
                job.error = error[:500]
                job.finished_at = datetime.now(timezone.utc)
                # Keep the linked Task in sync so the user sees the
                # failure on whichever surface they're looking at.
                if job.source_task_id:
                    t = db.session.get(Task, job.source_task_id)
                    if t and t.status == "in_progress":
                        t.status = "blocked"
                        t.updated_at = datetime.now(timezone.utc)
                        extra = dict(t.extra or {})
                        extra["kickoff_error"] = error[:500]
                        t.extra = extra
                db.session.commit()
                return

            # Legacy Task-keyed kickoff path.
            task = db.session.get(Task, kickoff_id)
            if task and task.status == "in_progress":
                task.status = "blocked"
                task.updated_at = datetime.now(timezone.utc)
                extra = dict(task.extra or {})
                extra["kickoff_error"] = error[:500]
                task.extra = extra
                db.session.commit()
    except Exception as e:
        logger.warning(f"[agent] Couldn't surface kickoff failure for {kickoff_id}: {e}")


def _kickoff_agent_headless(agent_id, worktree_path, task_id, branch_name=None, plan_first=False, role="coding"):
    """Start an autonomous agent as a background daemon thread — no terminal.

    Single entry point for every role (coding / review / investigation).
    `claude --print` runs in the worktree, tees its transcript to
    agent.log, and exits when the agent stops responding. The session
    id is registered so "View Session" can later `claude --resume`
    into it for the user to iterate alongside the agent.

    The initial prompt is role-specific:
      - coding: "work, reply ready_for_review on first commit, loop"
      - review: "execute the PR review skill from TASK.md, reply
        ready_for_review with the review content (PATTERN: /
        PROPOSAL: blocks live inside it), loop"
      - investigation: same shape, but for the investigate skill
        and INVESTIGATION.md output.

    For review/investigation, TASK.md must already contain the skill
    prompt (the caller embeds it before calling prepare()).

    plan_first=True starts the run in Claude's plan mode and prompts
    the agent to produce a markdown plan first — it calls
    reply(message_type="plan_for_approval") and exits without writing
    code. The user approves or requests changes in the plan UI; the
    approve endpoint resumes the session without plan mode so the
    agent can actually implement. Plan-first only applies to coding.

    Returns immediately after spawning the thread.
    """
    import shutil
    import threading

    claude_path = shutil.which("claude")
    if not claude_path:
        return {"success": False, "error": "claude CLI not found"}
    if branch_name and not _SAFE_BRANCH_RE.match(branch_name):
        return {"success": False, "error": f"Unsafe branch name: {branch_name!r}"}
    if _UNSAFE_PATH_CHARS.search(worktree_path):
        return {"success": False, "error": f"Unsafe worktree path: {worktree_path!r}"}

    # Kickoff concurrency guard: if a wake or a prior kickoff is still
    # running for this task, don't overwrite the session registry
    # mid-flight — the orphaned claude process would write to an
    # abandoned session_id and the user would lose its work.
    from planet_maiko.agents.wake import claim_task
    lock = claim_task(task_id)
    if lock is None:
        return {"success": False, "error": "Agent is already running for this task"}

    session_id = str(uuid.uuid4())
    from planet_maiko.api.agents_api import _set_session
    _set_session(task_id, session_id, worktree_path)

    if plan_first:
        initial_prompt = (
            "Read TASK.md and CLAUDE.md in this directory. Do NOT write "
            "any code yet. Produce a detailed implementation plan "
            "(markdown, 500 words max) covering: what you'll change, in "
            "which files, in what order, and the key decisions / risks. "
            "When the plan is ready, call reply(content=<your plan>, "
            "message_type=\"plan_for_approval\") via the maiko-channel "
            "MCP and exit. The user will approve or request changes; "
            "Maiko will resume you with their decision."
        )
    elif role == "review":
        initial_prompt = (
            "Read TASK.md and CLAUDE.md in this directory. TASK.md "
            "carries the PR review instructions and context — execute "
            "them following the review-agent-protocol in CLAUDE.md. "
            "When done, call reply(content=<your full review markdown, "
            "including any PATTERN: / PROPOSAL: blocks>, "
            "message_type=\"ready_for_review\") via the maiko-channel "
            "MCP. The server parses PATTERN: / PROPOSAL: blocks out "
            "of your content automatically — keep them inside the "
            "reply, not in stdout. After replying, check_inbox for "
            "any follow-up questions and iterate."
        )
    elif role == "investigation":
        initial_prompt = (
            "Read TASK.md and CLAUDE.md in this directory. TASK.md "
            "carries the investigation instructions and context — "
            "execute them following the investigation-agent-protocol "
            "in CLAUDE.md. When done, call reply(content=<your full "
            "investigation report markdown, including any PATTERN: / "
            "PROPOSAL: / CONFIDENCE: blocks>, "
            "message_type=\"ready_for_review\") via the maiko-channel "
            "MCP. After replying, check_inbox for any follow-up "
            "questions."
        )
    elif role == "cartographer":
        initial_prompt = (
            "Read CLAUDE.md in this directory — it carries the "
            "cartographer-agent-protocol. TASK.md names the repo "
            "you're mapping. Walk the tree (README, manifests, "
            "top-level dirs, entry points, recent git log, a few "
            "sample source files), then call reply(content=<your "
            "overview markdown>, message_type=\"insight\") via the "
            "maiko-channel MCP exactly once. The server auto-tags "
            "cartographer insights — no need to pass tags yourself. "
            "Read-only: do not commit, push, or modify anything. "
            "Exit after the reply."
        )
    else:  # coding
        initial_prompt = (
            "Read TASK.md and CLAUDE.md in this directory. Begin "
            "working on the task following the protocol. After your "
            "first meaningful commit, call reply(content=\"<one-line "
            "summary>\", message_type=\"ready_for_review\") via the "
            "maiko-channel MCP and then use check_inbox to receive "
            "any review feedback. Do not git push or open PRs — "
            "Maiko handles that once the human approves."
        )

    # No --allowedTools alongside --dangerously-skip-permissions — the
    # skip flag is the blanket bypass, and passing allowlists on top
    # gets treated as restrictive scope filter that stalls on writes
    # to unlisted paths and MCP subtools. Worktree isolation makes the
    # nuclear option safe here.
    cmd = [
        claude_path, "--print", "--output-format", "text",
        "--session-id", session_id,
        "--dangerously-skip-permissions",
    ]

    # Headless `claude --print` stopped auto-discovering .mcp.json
    # reliably in recent CLI versions — without an explicit
    # --mcp-config the worktree's project servers (maiko-channel +
    # whatever inherited from the parent repo) silently don't load.
    # The agent then "can't connect to the Maiko MCP" because there
    # IS no maiko-channel registered. Pointing at the worktree's
    # .mcp.json explicitly is what the docs recommend post Feb 2026.
    mcp_config_path = os.path.join(worktree_path, ".mcp.json")
    if os.path.exists(mcp_config_path):
        cmd.extend(["--mcp-config", mcp_config_path])

    # Effort level: route through resolve_effort so the per-task rule
    # in routing.effort_rules wins over the global thinking_budget.
    # Coding agents default to "high" — they benefit visibly from
    # deeper reasoning. Cartographers / investigators use the same
    # role key for now (they're long-running too); the user can
    # split them in Settings if they want.
    try:
        from planet_maiko.agents.routing import resolve_effort
        budget = resolve_effort("coding_agent") or "medium"
    except Exception:
        budget = "medium"
    if budget in ("low", "medium", "high", "max"):
        cmd.extend(["--effort", budget])

    if plan_first or role == "cartographer":
        # Claude's plan mode restricts the tool set to read-only
        # (Read/Glob/Grep/etc.), so the agent can't write even if its
        # prompt discipline slips. Reply via MCP still works since
        # MCP tools aren't disk-modifying. Cartographers run read-only
        # by design — no exceptions.
        cmd.extend(["--permission-mode", "plan"])

    log_path = os.path.join(worktree_path, "agent.log")

    # Grab the Flask app so the daemon thread can flip agent state
    # (idle → working → idle) via wake.set_agent_state, which needs
    # an app context to touch the DB.
    try:
        from flask import current_app
        _app = current_app._get_current_object()
    except RuntimeError:
        _app = None

    def _run():
        from planet_maiko.agents.wake import set_agent_state
        set_agent_state(_app, task_id, "working")
        popen = None
        crash_error = None  # captured to mark the AgentJob/Task failed
        try:
            with open(log_path, "w", encoding="utf-8") as log:
                log.write(f"# Headless coding agent run\n# session_id: {session_id}\n\n")
                log.flush()
                # Popen (not subprocess.run) so stop_agent_session can
                # reach into _running_processes and terminate in-flight
                # when the user cancels a task. Communicate() still
                # blocks this thread until the subprocess exits, so the
                # set_agent_state("idle") + lock.release() happen at
                # the right moment.
                popen = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=worktree_path,
                )
                register_running_process(task_id, popen)
                try:
                    popen.communicate(input=initial_prompt)
                finally:
                    unregister_running_process(task_id)
            # Non-zero exit = claude crashed (MCP load failure, auth
            # missing, network blip, etc). Capture the last bit of the
            # log so the failure surfaces somewhere useful instead of
            # vanishing into agent.log nobody reads.
            if popen is not None and popen.returncode not in (None, 0):
                crash_error = (
                    f"claude exited {popen.returncode}: "
                    + _tail_log(log_path, max_chars=400)
                )
        except Exception as e:
            crash_error = f"kickoff thread crashed: {e}"
            logger.warning(f"[agent] Headless run for {task_id} failed: {e}")
        finally:
            if popen is not None:
                unregister_running_process(task_id)
            set_agent_state(_app, task_id, "idle")
            lock.release()
            # If the subprocess died before the agent could report back,
            # nothing else moves the AgentJob/Task off "running" — flip
            # it to "failed" here so the user sees what went wrong
            # instead of a row stuck mid-flight.
            if crash_error:
                _mark_kickoff_failed(_app, task_id, crash_error)

    threading.Thread(target=_run, daemon=True, name=f"coding-{task_id}").start()
    logger.info(f"[agent] Headless coding agent launched for {agent_id} (session {session_id[:8]})")
    return {
        "success": True,
        "working_path": worktree_path,
        "session_id": session_id,
        "mode": "headless",
        "log_path": log_path,
    }

