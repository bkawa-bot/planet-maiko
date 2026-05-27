"""Runtime dispatch + synchronous LLM calls for Maiko itself.

This module is the entry point everything in the app uses when Maiko (not
a coding agent) needs to think. It owns three things:

1. The runtime registry — lazy-instantiates and caches ClaudeCodeRuntime /
   TmuxClaudeRuntime / OllamaRuntime, and routes `_get_runtime(task_type)`
   through `agents/routing.py` so per-task runtime rules apply.

2. Skill execution — `run_skill` (generic) and `run_skill_as_agent` (with
   agent persona, protocol, and team role instructions prepended). Used
   for investigations, brainstorms, morning briefs, etc.

3. One-shot task execution — `execute_one_shot_task` drives a single
   review / investigation / repo_analysis / cartograph task end-to-end:
   builds context, runs the skill, parses output blocks, writes the
   result pupdate, marks the task done.

Long-running coding agents do NOT go through here. They're driven by
`agents/wake.py` (turn lifecycle) and `api/agent_outbox.py` (terminal
reply handling), both of which call `_get_runtime` to pick a runtime.
"""

import logging
import uuid
from datetime import datetime, timezone

from planet_maiko.config import load_config
from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

# Per-name runtime cache so we instantiate each class at most once
# across the app's lifetime. _runtime tracks the default runtime;
# _runtimes is the per-name registry that supports task-aware lookups.
_runtime = None
_runtimes = {}


def _instantiate_runtime(name):
    """Build a fresh runtime instance by name. Handles startup hooks
    (tmux orphan cleanup).
    """
    from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
    if name == "claude-code":
        return ClaudeCodeRuntime()
    if name == "claude-code-tmux":
        try:
            from planet_maiko.agents.runtimes.tmux_claude import (
                TmuxClaudeRuntime,
                cleanup_orphan_sessions,
            )
            instance = TmuxClaudeRuntime()
            if instance.is_available():
                try:
                    n = cleanup_orphan_sessions()
                    if n:
                        logger.info(f"[brain] cleaned {n} orphan tmux session(s) at startup")
                except Exception as e:
                    logger.debug(f"[brain] orphan tmux cleanup skipped: {e}")
            return instance
        except Exception as e:
            logger.warning(f"[brain] couldn't load TmuxClaudeRuntime ({e})")
            return None
    if name == "ollama":
        try:
            from planet_maiko.agents.runtimes.ollama import OllamaRuntime
            return OllamaRuntime()
        except Exception as e:
            logger.warning(f"[brain] couldn't load OllamaRuntime ({e})")
            return None
    logger.warning(f"[brain] unknown runtime name: {name!r}")
    return None


def _get_runtime_by_name(name):
    if name not in _runtimes:
        instance = _instantiate_runtime(name)
        _runtimes[name] = instance
    return _runtimes[name]


def _default_runtime_name():
    # Default to the tmux-backed Claude runtime: interactive sessions,
    # plays nicely with Claude Code subscriptions, and the session
    # pop-out from the agent job page actually attaches into something
    # real. The headless "claude-code" runtime is still selectable via
    # config (brain.runtime) or per-task routing rules; when tmux
    # isn't available on the host, _instantiate_runtime returns None
    # and the caller falls back to ClaudeCodeRuntime automatically.
    try:
        cfg = load_config()
        return (cfg.get("brain") or {}).get("runtime", "claude-code-tmux")
    except Exception:
        return "claude-code-tmux"


def _get_runtime(task_type=None):
    """Get the right agent runtime for ``task_type``.

    Without ``task_type``: returns the default runtime configured at
    ``brain.runtime`` (default: "claude-code-tmux"; "claude-code"
    selects the headless Agent SDK pool instead).
    All existing callers that don't pass a task_type continue to get
    the same behavior as before.

    With ``task_type``: looks up ``routing.runtime_rules[task_type]``
    (falling through to ``DEFAULT_RUNTIME`` and prefix-match in
    routing.py). If a task-specific runtime is configured AND is
    available on this machine, returns it. Otherwise falls back to
    the default runtime, so a misconfigured Ollama (server down, not
    installed) silently routes back to Claude rather than failing
    the call.

    Supported runtime names:
      "claude-code"       headless `claude --print` (Agent SDK pool)
      "claude-code-tmux"  interactive claude in tmux (subscription pool, Mac)
      "ollama"            local OpenAI-compatible server (no
                          Anthropic spend; sync-only, can't drive
                          coding agents)
    """
    default_name = _default_runtime_name()

    if task_type:
        try:
            from planet_maiko.agents.routing import resolve_runtime
            routed_name = resolve_runtime(task_type)
        except Exception:
            routed_name = None

        if routed_name and routed_name != default_name:
            routed = _get_runtime_by_name(routed_name)
            if routed is not None and routed.is_available():
                return routed
            logger.info(
                f"[brain] {routed_name} unavailable for task '{task_type}'; "
                f"falling back to {default_name}"
            )

    runtime = _get_runtime_by_name(default_name)
    if runtime is None:
        # Last-ditch: build a plain ClaudeCodeRuntime so the caller
        # always gets back *something* with the right interface,
        # even if its is_available is False. _get_runtime never
        # returns None.
        from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
        runtime = ClaudeCodeRuntime()
        _runtimes[default_name] = runtime
    if not runtime.is_available():
        logger.warning(f"[brain] {runtime.name} runtime is not available")
    return runtime


def run_skill(skill_name, context=None, working_dir=None):
    """Run a named skill through the brain runtime.

    Args:
        skill_name: the skill to run (e.g. "investigate", "brainstorm")
        context: dict of context data the skill needs
        working_dir: directory to run in (for repo-aware skills)

    Returns:
        dict with:
            - output: str (the skill's text output)
            - success: bool
            - error: str or None
    """
    from planet_maiko.agents.skills import get_skill_prompt

    runtime = _get_runtime()
    if not runtime.is_available():
        return {"output": "", "success": False, "error": "Brain runtime not available"}

    prompt = get_skill_prompt(skill_name, context or {})
    if prompt is None:
        return {"output": "", "success": False, "error": f"Unknown skill: {skill_name}"}

    # Skills can take longer - give them more time
    timeout = 600 if skill_name in ("investigate", "repo-analysis") else 120

    from planet_maiko.agents.routing import resolve_model, resolve_effort
    # Release DB before long LLM call to avoid SQLite locks
    db.session.close()
    task_type = f"skill:{skill_name}"
    result = runtime.send(
        prompt, working_dir=working_dir, timeout=timeout,
        model=resolve_model(task_type), effort=resolve_effort(task_type),
    )

    # Auto-save investigation results as pupdates for easy review
    if skill_name == "investigate" and result.get("success"):
        try:
            investigation = Pupdate(
                id=f"investigation-{uuid.uuid4().hex[:8]}",
                source="maiko",
                type="investigation",
                priority="normal",
                title=f"Investigation: {(context or {}).get('query', 'Unknown')[:100]}",
                body=result.get("output", "")[:5000],
                actionable=True,
                action_hint="Review investigation",
                tags=["investigation", "maiko"],
            )
            db.session.add(investigation)
            db.session.commit()
        except Exception:
            pass  # Don't fail the skill if pupdate creation fails

    return result


def run_skill_as_agent(agent_profile_id, skill_name, context=None, working_dir=None, session_id=None, skip_permissions=False):
    """Run a skill attributed to a specific agent profile.

    Wraps run_skill and prepends the agent's markdown instructions to the
    prompt so the model adopts the agent's persona / rules for this
    session. If the profile has no instructions, this is equivalent to
    calling run_skill directly.
    """
    from planet_maiko.models.agent_profile import AgentProfile
    profile = db.session.get(AgentProfile, agent_profile_id) if agent_profile_id else None
    if not profile:
        return run_skill(skill_name, context, working_dir)

    runtime = _get_runtime()
    if not runtime.is_available():
        return {"output": "", "success": False, "error": "Brain runtime not available"}

    from planet_maiko.agents.skills import get_skill_prompt
    prompt = get_skill_prompt(skill_name, context or {})
    if prompt is None:
        return {"output": "", "success": False, "error": f"Unknown skill: {skill_name}"}

    # Prepend the agent's persona + instructions so their voice and rules
    # shape every response. Generic defaults are used when fields are
    # empty so the model always sees a consistent preamble.
    preamble_lines = [
        f"You are {profile.display_name or 'an agent'}, a Planet Maiko {profile.role} agent.",
    ]
    if profile.scope_repo:
        preamble_lines.append(f"You specialize in the {profile.scope_repo} repository.")
    if profile.flavor_text:
        preamble_lines.append(profile.flavor_text)
    if profile.instructions:
        preamble_lines.append("\nYour instructions:")
        preamble_lines.append(profile.instructions.strip())
    preamble = "\n".join(preamble_lines) + "\n\n---\n\n"

    # Role-specific communication protocol. One-shot agents (review,
    # investigation) can't call a CLI or open an MCP channel, so the
    # "protocol" is a set of structured blocks they include in their
    # output which the server parses out after the call returns.
    # Injected inline here — there's no other delivery channel for a
    # single-send LLM call.
    protocol = ""
    protocol_filename = {
        "review": "review-agent-protocol.md",
        "investigation": "investigation-agent-protocol.md",
    }.get(profile.role)
    if protocol_filename:
        from pathlib import Path
        proto_path = (Path(__file__).resolve().parent.parent
                      / "prompts" / protocol_filename)
        try:
            if proto_path.is_file():
                protocol = proto_path.read_text(encoding="utf-8") + "\n\n---\n\n"
        except Exception:
            # If the file is missing or unreadable, proceed without —
            # the agent will still produce its main output, just without
            # the structured blocks the server would have parsed.
            logger.debug(f"[brain] Could not load {protocol_filename}")

    # Team-wide role instructions from Settings > Agents. Sit between
    # the machine-contract protocol and the individual agent's
    # personality so "every reviewer cares about X" applies across
    # every review agent without editing each one.
    team_role = ""
    try:
        from planet_maiko.config import load_config
        cfg = load_config().get("agents", {}) or {}
        team_role_text = ((cfg.get("role_instructions") or {}).get(profile.role) or "").strip()
        if team_role_text:
            team_role = f"## Team instructions for {profile.role} agents\n\n{team_role_text}\n\n---\n\n"
    except Exception:
        pass

    full_prompt = preamble + protocol + team_role + prompt

    timeout = 600 if skill_name in ("investigate", "repo-analysis") else 120
    from planet_maiko.agents.routing import resolve_model, resolve_effort
    db.session.close()
    task_type = f"skill:{skill_name}"
    result = runtime.send(full_prompt, working_dir=working_dir, timeout=timeout,
                          model=resolve_model(task_type),
                          effort=resolve_effort(task_type),
                          session_id=session_id,
                          skip_permissions=skip_permissions)
    # Refresh the profile since session closed — update last_active_at.
    profile = db.session.get(AgentProfile, agent_profile_id)
    if profile:
        profile.last_active_at = datetime.now(timezone.utc)
        db.session.commit()
    return result


# Task type → (required role, skill name). Shared by the cycle's
# auto-execution phase and the manual /tasks/<id>/launch endpoint.
ONE_SHOT_ROLE_FOR_TYPE = {
    "investigation": ("investigation", "investigate"),
    "repo_analysis": ("investigation", "repo-analysis"),
    "review": ("review", "pr-review"),
    "pr_review": ("review", "pr-review"),
    # Registering cartograph here lets the cycle's execute phase pick
    # up cartograph tasks that enter via the proposal path
    # (role_autonomy → agent_proposal → approve_proposal), using the
    # same prepare + headless kickoff machinery as the manual
    # /insights/cartograph endpoint. The skill name "cartograph" is
    # unused by the execute phase (which passes role, not skill), but
    # kept here for symmetry.
    "cartograph": ("cartographer", "cartograph"),
}


def execute_one_shot_task(task, working_dir=None):
    """Run a single review/investigation/repo_analysis task as its
    assigned agent, parse output blocks, publish a result pupdate, and
    mark the task done.

    Args:
        task: the Task to run.
        working_dir: optional path to run the skill in — typically a
            worktree prepared by runtime.prepare() so the agent has
            repo access and the user can attach later to "dig deeper".
            If None, falls back to task.extra.working_path, otherwise
            no working_dir (skill runs in the default cwd).

    Returns dict with:
        success: bool
        status: final task.status ("done" or "new" on retryable fail)
        artifact: str (cleaned output), or None
        patterns_emitted / proposals_emitted: ints
        confidence: "low"|"medium"|"high"|None
        error: str or None
    """
    import uuid as _uuid
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.brain.learning.agent_output import parse_and_apply_blocks

    mapping = ONE_SHOT_ROLE_FOR_TYPE.get(task.type)
    if not mapping:
        return {"success": False, "status": task.status,
                "error": f"Task type '{task.type}' isn't a one-shot role"}
    role, skill_name = mapping

    if not task.assigned_agent_id:
        return {"success": False, "status": task.status, "error": "No agent assigned"}
    agent = db.session.get(AgentProfile, task.assigned_agent_id)
    if not agent:
        return {"success": False, "status": task.status, "error": "Assigned agent not found"}
    if agent.role != role:
        return {"success": False, "status": task.status,
                "error": f"Agent role '{agent.role}' doesn't match task type '{task.type}'"}

    task.status = "in_progress"
    db.session.commit()

    meta = task.extra or {}
    from planet_maiko.brain.automations import format_pupdate_for_context
    pupdate_block = format_pupdate_for_context(meta.get("pupdate_snapshot"))
    context_parts = [pupdate_block]
    if task.url:
        context_parts.append(f"URL: {task.url}")
    if meta.get("repo"):
        context_parts.append(f"Repo: {meta['repo']}")
    context = {
        "query": task.title,
        "context": "\n\n".join(p for p in context_parts if p),
        "pupdates": "[]", "tasks": "[]", "calendar": "[]",
    }

    resolved_working_dir = working_dir or meta.get("working_path")

    # Generate + register a session id so the background `claude --print`
    # run saves its transcript under a predictable path, and "View Session"
    # / `claude --resume <id>` can find it both live and after completion.
    session_id = meta.get("session_id") or str(_uuid.uuid4())
    if not meta.get("session_id"):
        task.extra = {**meta, "session_id": session_id}
        db.session.commit()
    if resolved_working_dir:
        try:
            from planet_maiko.api.agents_api import _set_session
            _set_session(task.id, session_id, resolved_working_dir)
        except Exception:
            pass

    try:
        # Autonomous runs have no human to approve tool use. The
        # worktree is isolated (throwaway checkout we created just
        # for this task), so skipping permission prompts is safe.
        result = run_skill_as_agent(agent.id, skill_name, context=context,
                                    working_dir=resolved_working_dir,
                                    session_id=session_id,
                                    skip_permissions=True)
    except Exception as e:
        logger.warning(f"[execute] Task {task.id} run failed: {e}")
        task.status = "new"
        task.extra = {**(task.extra or {}), "last_error": str(e)[:200]}
        db.session.commit()
        return {"success": False, "status": "new", "error": str(e)[:200]}

    if not result or not result.get("success") or not result.get("output"):
        task.status = "new"
        err = (result or {}).get("error", "no output")
        task.extra = {**(task.extra or {}), "last_error": err[:200]}
        db.session.commit()
        return {"success": False, "status": "new", "error": err}

    raw_output = result["output"]
    parsed = parse_and_apply_blocks(
        raw_output, agent=agent, task=task,
        repo=(task.extra or {}).get("repo"),
    )
    output = parsed["cleaned_output"]

    task.status = "done"
    task.extra = {
        **(task.extra or {}),
        "artifact": output[:16000],
        "completed_by": agent.id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "patterns_emitted": parsed["patterns_emitted"],
        "proposals_emitted": parsed["proposals_emitted"],
        "confidence": parsed["confidence"],
    }

    result_type = "pr_review_complete" if role == "review" else "investigation_complete"
    action_hint = "Open review" if role == "review" else "Open investigation"
    title_prefix = "Review ready" if role == "review" else "Investigation ready"
    pri = "high" if parsed["confidence"] == "low" else "normal"

    result_pupdate = Pupdate(
        id=f"{role}-result-{_uuid.uuid4().hex[:8]}",
        source="maiko",
        type=result_type,
        priority=pri,
        title=f"{title_prefix}: {task.title}",
        body=output[:8000],
        url=task.url,
        actionable=True,
        action_hint=action_hint,
        tags=[role, "maiko", agent.id] + (["low_confidence"] if parsed["confidence"] == "low" else []),
        # Carry task_id + agent_id forward so surfaces like the Pack
        # Requests widget can route straight to the right diff /
        # report page without an extra task-lookup round-trip.
        extra={"task_id": task.id, "agent_id": agent.id},
    )
    db.session.add(result_pupdate)

    agent.tasks_completed = (agent.tasks_completed or 0) + 1
    agent.last_active_at = datetime.now(timezone.utc)
    db.session.commit()

    return {
        "success": True,
        "status": "done",
        "artifact": output,
        "patterns_emitted": parsed["patterns_emitted"],
        "proposals_emitted": parsed["proposals_emitted"],
        "confidence": parsed["confidence"],
    }


def get_status():
    """Get brain session status."""
    runtime = _get_runtime()
    return {
        "runtime": runtime.get_info(),
        "available": runtime.is_available(),
    }
