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


def triage_pupdate(pupdate):
    """Ask the brain to decide what to do with an unmatched pupdate.

    Returns:
        dict with:
            - action: "dismiss", "create_task", "mark_read", "skip"
            - reason: str explaining the decision
            - task_title: str (if action is create_task)
            - task_priority: str (if action is create_task)
    """
    runtime = _get_runtime()
    if not runtime.is_available():
        return {"action": "skip", "reason": "Brain runtime not available"}

    prompt = f"""You are a notification triage assistant. Analyze this notification and decide what to do.

Notification:
- Source: {pupdate.source}
- Type: {pupdate.type}
- Priority: {pupdate.priority}
- Title: {pupdate.title}
- Body: {pupdate.body or '(none)'}
- Tags: {', '.join(pupdate.tags or [])}
- Actionable: {pupdate.actionable}
- Action hint: {pupdate.action_hint or '(none)'}

Decide ONE action:
- "dismiss": This is noise, not relevant, or already handled
- "create_task": This requires work - create a task for it
- "mark_read": Informational only, no action needed
- "skip": Unsure, leave for the user to decide

Respond with JSON:
{{"action": "...", "reason": "...", "task_title": "...", "task_priority": "low|normal|high|urgent"}}

task_title and task_priority are only needed if action is "create_task"."""

    from planet_maiko.agents.routing import resolve_model
    # Release DB before LLM call to avoid SQLite locks
    db.session.close()
    result = runtime.send_json(prompt, timeout=30, model=resolve_model("triage"))

    if not result["success"] or not result.get("parsed"):
        logger.warning(f"[brain] Triage failed for {pupdate.id}: {result.get('error')}")
        return {"action": "skip", "reason": f"Triage failed: {result.get('error')}"}

    return result["parsed"]


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


def run_skill_as_agent(agent_profile_id, skill_name, context=None, working_dir=None):
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
                          model=resolve_model(f"skill:{skill_name}"))
    # Refresh the profile since session closed — update last_active_at.
    profile = db.session.get(AgentProfile, agent_profile_id)
    if profile:
        profile.last_active_at = datetime.now(timezone.utc)
        db.session.commit()
    return result


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
