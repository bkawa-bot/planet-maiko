"""Headless kickoff for any agent role.

Spawns a detached background thread that runs an autonomous agent
session in a prepared worktree. The actual subprocess (currently
`claude --print` via ClaudeCodeRuntime; future backends like Aider /
Codex / Goose plug in here via the same AgentRuntime.spawn()
contract) is delegated to the runtime — this module only handles the
parts that don't depend on the model: the role-specific initial
prompt, the per-job concurrency lock, the daemon thread, the
Flask-app-context dance for DB writes, and the AgentJob/Task state
transitions when the run finishes.

The subprocess + cancellation tracking + log capture live on the
runtime (agents/runtimes/claude_code.py:spawn). See
docs/AGENT_RUNTIME.md.
"""

import logging
import os
import re
import threading
import uuid

logger = logging.getLogger(__name__)


_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
# Path validation: catch shell metacharacters but allow real path chars
# (letters, digits, spaces, dot, dash, underscore, slash, backslash, colon, paren).
_UNSAFE_PATH_CHARS = re.compile(r'[;&|`$<>!"*?\n\r]')


def _mark_kickoff_failed(app, kickoff_id, error):
    """Mark the AgentJob (or Task) tied to this kickoff as failed.

    `kickoff_id` is what the caller passed as task_id. Typically an
    AgentJob.id (`job-...`); Task-keyed runs send a Task.id. We try
    AgentJob first since that's the canonical target.
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

            # Task-keyed kickoff path.
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


def _kickoff_agent_headless(agent_id, worktree_path, job_id, branch_name=None, plan_first=False, role="coding"):
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
    if branch_name and not _SAFE_BRANCH_RE.match(branch_name):
        return {"success": False, "error": f"Unsafe branch name: {branch_name!r}"}
    if _UNSAFE_PATH_CHARS.search(worktree_path):
        return {"success": False, "error": f"Unsafe worktree path: {worktree_path!r}"}

    # Resolve the routing key from AgentType.model_routing_key (falls
    # back to "coding_agent" when missing — preserves legacy behavior
    # for the four built-ins, all seeded with model_routing_key=
    # "coding_agent" today).
    from planet_maiko.agent_types import model_routing_key_for
    routing_key = model_routing_key_for(role)

    # Pass the routing_key so the Settings → Model Routing override
    # rule actually picks the runtime. Without this the spawn would
    # always use brain.runtime and a user who set "Coding agents → tmux"
    # via the per-task routing UI would silently still get the default.
    from planet_maiko.agents.brain_session import _get_runtime
    runtime = _get_runtime(routing_key)
    if not runtime.is_available():
        return {"success": False, "error": f"{runtime.name} runtime not available"}
    if not runtime.supports_spawn():
        return {"success": False, "error": f"{runtime.name} runtime can't drive autonomous agents"}

    # Kickoff concurrency guard: if a wake or a prior kickoff is still
    # running for this job, don't overwrite the session registry
    # mid-flight — the orphaned claude process would write to an
    # abandoned session_id and the user would lose its work.
    from planet_maiko.agents.wake import claim_task
    lock = claim_task(job_id)
    if lock is None:
        return {"success": False, "error": "Agent is already running for this job"}

    session_id = str(uuid.uuid4())
    from planet_maiko.api.agents_api import _set_session
    _set_session(job_id, session_id, worktree_path)

    initial_prompt = _initial_prompt_for(role, plan_first=plan_first)

    # Model + effort routing: pull both from config so the agent runs
    # on whatever the user picked in Settings → Model Routing. The
    # routing key per-role comes from AgentType.model_routing_key
    # above; the four built-ins all default to "coding_agent" so the
    # legacy single-rule behavior is preserved, but a custom type can
    # opt into its own routing slot.
    try:
        from planet_maiko.agents.routing import resolve_model, resolve_effort
        model = resolve_model(routing_key)
        effort = resolve_effort(routing_key) or "medium"
    except Exception:
        model = None
        effort = "medium"

    # Permission mode: AgentType.permission_mode is the canonical
    # source. The four built-ins are seeded with the historical
    # behavior (cartographer="plan"; everyone else=None). User-defined
    # types declare their own value. plan_first overrides for any
    # role that supports it (only "coding" today).
    from planet_maiko.agent_types import get_agent_type as _get_agent_type
    agent_type = _get_agent_type(role)
    permission_mode = agent_type.permission_mode if agent_type else None
    if plan_first:
        permission_mode = "plan"

    # If a .mcp.json was written for inherited project MCPs, point
    # claude at it. With maiko-channel removed, this is only present
    # when the user has Linear / GitHub / etc. configured.
    mcp_config_path = os.path.join(worktree_path, ".mcp.json")
    if not os.path.exists(mcp_config_path):
        mcp_config_path = None

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
        set_agent_state(_app, job_id, "working")
        crash_error = None
        try:
            result = runtime.spawn(
                worktree_path,
                initial_prompt,
                session_id,
                job_id=job_id,
                mcp_config_path=mcp_config_path,
                log_path=log_path,
                model=model,
                effort=effort,
                permission_mode=permission_mode,
                # MAIKO_JOB_ID flows into the subprocess env so the
                # agent can call `maiko reply / inbox / check-code /
                # leave-comment` from inside its shell without passing
                # --job every time.
                extra_env={"MAIKO_JOB_ID": job_id},
            )
            if not result.get("success"):
                crash_error = result.get("error") or "unknown spawn failure"
                tail = result.get("log_tail")
                if tail:
                    crash_error = f"{crash_error} ({tail})"
        except Exception as e:
            crash_error = f"kickoff thread crashed: {e}"
            logger.warning(f"[agent] Headless run for {job_id} failed: {e}")
        finally:
            set_agent_state(_app, job_id, "idle")
            lock.release()
            # If the subprocess died before the agent could report back,
            # nothing else moves the AgentJob/Task off "running" — flip
            # it to "failed" here so the user sees what went wrong
            # instead of a row stuck mid-flight.
            if crash_error:
                _mark_kickoff_failed(_app, job_id, crash_error)

    threading.Thread(target=_run, daemon=True, name=f"agent-{job_id}").start()
    logger.info(
        f"[agent] Headless {role} agent launched for {agent_id} "
        f"(session {session_id[:8]}, runtime {runtime.name})"
    )
    return {
        "success": True,
        "working_path": worktree_path,
        "session_id": session_id,
        "mode": "headless",
        "log_path": log_path,
    }


def _initial_prompt_for(role, plan_first=False):
    """Build the role-specific initial prompt the agent receives at
    boot. CLI-only — no MCP-tool references — matches the protocol
    files in prompts/."""
    if plan_first:
        return (
            "Read TASK.md and CLAUDE.md in this directory. Do NOT write "
            "any code yet. Produce a detailed implementation plan "
            "(markdown, 500 words max) covering: what you'll change, in "
            "which files, in what order, and the key decisions / risks. "
            "When the plan is ready, run `maiko reply \"<your plan>\" "
            "--type plan_for_approval` and exit. The user will approve "
            "or request changes; Maiko will resume you with their decision."
        )
    if role == "review":
        return (
            "Read TASK.md and CLAUDE.md in this directory. TASK.md "
            "carries the PR review instructions and context — execute "
            "them following the review-agent-protocol in CLAUDE.md. "
            "When done, run `maiko reply \"<your full review markdown, "
            "including any PATTERN: / PROPOSAL: blocks>\" "
            "--type ready_for_review`. The server parses PATTERN: / "
            "PROPOSAL: blocks out of your content automatically — keep "
            "them inside the reply, not in stdout. The Stop hook will "
            "auto-poll your inbox for any follow-up questions before "
            "you settle."
        )
    if role == "investigation":
        return (
            "Read TASK.md and CLAUDE.md in this directory. TASK.md "
            "carries the investigation instructions and context — "
            "execute them following the investigation-agent-protocol "
            "in CLAUDE.md. When done, run `maiko reply \"<your full "
            "investigation report markdown, including any PATTERN: / "
            "PROPOSAL: / CONFIDENCE: blocks>\" --type ready_for_review`. "
            "The Stop hook will auto-poll your inbox for any follow-up "
            "questions before you settle."
        )
    if role == "cartographer":
        return (
            "Read CLAUDE.md in this directory — it carries the "
            "cartographer-agent-protocol. TASK.md names the repo "
            "you're mapping. Walk the tree (README, manifests, "
            "top-level dirs, entry points, recent git log, a few "
            "sample source files), then run `maiko reply \"<your "
            "overview markdown>\" --type insight` exactly once. The "
            "server auto-tags cartographer insights — no need to pass "
            "tags yourself. Read-only: do not commit, push, or modify "
            "anything. Exit after the reply."
        )
    # coding (default)
    return (
        "Read TASK.md and CLAUDE.md in this directory. Begin working "
        "on the task following the protocol. After your first "
        "meaningful commit, run `maiko check-code` to verify the "
        "mechanical checks are green, then run `maiko reply \"<one-line "
        "summary>\" --type ready_for_review`. The Stop hook will auto-"
        "poll your inbox for review feedback. Do not git push or open "
        "PRs — Maiko handles that once the human approves."
    )

