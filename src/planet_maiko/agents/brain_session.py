"""Brain session - the single persistent agent that handles all LLM reasoning.

The brain session is responsible for:
    1. Triage: deciding what to do with unmatched pupdates
    2. Skills: running investigation, brainstorm, morning brief, etc.
    3. Deciding when to spawn coding agents

It uses the configured runtime (Claude Code by default) and communicates
results back through the database (pupdates, tasks, etc.).
"""

import json
import logging
import uuid
from datetime import datetime, timezone

from planet_maiko.config import load_config
from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

# Lazy-loaded runtime instance
_runtime = None


def _get_runtime():
    """Get the configured agent runtime via entry_points plugin discovery."""
    global _runtime
    if _runtime is not None:
        return _runtime

    from importlib.metadata import entry_points

    config = load_config()
    brain_config = config.get("brain", {})
    runtime_name = brain_config.get("runtime", "claude-code")

    eps = entry_points(group="planet_maiko.runtimes")
    for ep in eps:
        if ep.name == runtime_name:
            runtime_cls = ep.load()
            _runtime = runtime_cls()
            break
    else:
        available = [ep.name for ep in eps]
        raise ValueError(f"Unknown runtime: '{runtime_name}'. Available: {available}")

    if not _runtime.is_available():
        logger.warning(f"[brain] Runtime '{runtime_name}' is not available")

    return _runtime


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
    timeout = 600 if skill_name in ("investigate", "brainstorm", "repo-analysis") else 120

    from planet_maiko.agents.routing import resolve_model
    # Release DB before long LLM call to avoid SQLite locks
    db.session.close()
    result = runtime.send(prompt, working_dir=working_dir, timeout=timeout, model=resolve_model(f"skill:{skill_name}"))

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

    The agent's LoRA adapter path (profile.extra.adapter_path) is noted
    in the context for future inference-time wiring — today it's not
    actually loaded for skill runs (that's only used by the pre-commit
    hook). This preserves the hook semantics while letting us attribute
    work to a profile.
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

    timeout = 600 if skill_name in ("investigate", "brainstorm", "repo-analysis") else 120
    from planet_maiko.agents.routing import resolve_model
    db.session.close()
    result = runtime.send(full_prompt, working_dir=working_dir, timeout=timeout,
                          model=resolve_model(f"skill:{skill_name}"),
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
    # Cartograph tasks were previously kicked off only by the manual
    # /insights/cartograph endpoint. Registering the type here lets the
    # cycle's execute phase pick up cartograph tasks that enter via the
    # proposal path (role_autonomy → agent_proposal → approve_proposal),
    # using the same prepare + headless kickoff machinery as the manual
    # route. The skill name "cartograph" is unused by the execute phase
    # (which passes role, not skill), but kept here for symmetry.
    "cartograph": ("cartographer", "cartograph"),
}


def execute_one_shot_task(task, working_dir=None):
    """Run a single review/investigation/repo_analysis task as its
    assigned agent, parse output blocks, publish a result pupdate, and
    mark the task done.

    Args:
        task: the Task to run.
        working_dir: optional path to run the skill in — typically a
            worktree prepared by coding_agent.prepare() so the agent has
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
    context = {
        "query": task.title,
        "context": f"URL: {task.url or ''}\nRepo: {meta.get('repo', '')}",
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


def reorder_tasks_with_hint(tasks, instructions):
    """Ask the brain to reorder tasks given a free-text user instruction.

    Args:
        tasks: list of dicts with keys id, title, priority, status, type
        instructions: free-text hint from the user (e.g. "prioritize reliability work")

    Returns:
        dict with keys:
            success: bool
            ordered_ids: list[str] in the LLM's chosen order (or [] on failure)
            error: str or None
    """
    runtime = _get_runtime()
    if not runtime.is_available():
        return {"success": False, "ordered_ids": [], "error": "Brain runtime not available"}

    prompt = (
        "You are reordering a personal task list based on a user's directive.\n\n"
        f"User directive: {instructions}\n\n"
        "Tasks:\n"
        f"{json.dumps(tasks, indent=2)}\n\n"
        "Return a JSON array of task IDs in the new priority order (most important first). "
        "Include every task ID exactly once. No other keys, no commentary."
    )

    from planet_maiko.agents.routing import resolve_model
    db.session.close()
    result = runtime.send_json(prompt, timeout=90, model=resolve_model("skill"))

    if not result.get("success"):
        return {"success": False, "ordered_ids": [], "error": result.get("error")}

    parsed = result.get("parsed")
    if not isinstance(parsed, list):
        return {"success": False, "ordered_ids": [], "error": "LLM did not return a list"}

    valid_ids = {t["id"] for t in tasks}
    ordered = [x for x in parsed if isinstance(x, str) and x in valid_ids]
    for t in tasks:
        if t["id"] not in ordered:
            ordered.append(t["id"])
    return {"success": True, "ordered_ids": ordered, "error": None}


def get_status():
    """Get brain session status."""
    runtime = _get_runtime()
    return {
        "runtime": runtime.get_info(),
        "available": runtime.is_available(),
    }
