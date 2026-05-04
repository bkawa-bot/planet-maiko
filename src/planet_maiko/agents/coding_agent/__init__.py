"""Coding agent orchestrator - prepares everything an agent needs to work.

Planet Maiko doesn't control the agent runtime. It controls the
surrounding orchestration:

    1. Prepare - create worktree, write TASK.md and CLAUDE.md
    2. Notify - tell the user an agent task is ready to launch
    3. Monitor - watch for agent pupdates via the API
    4. Collect - when agent reports done, update task status

The agent communicates back by calling the Planet Maiko API, either
directly or through the `maiko` CLI:

    maiko report "Finished implementing OAuth"
    maiko task done

This package was split out of the original 1300-line coding_agent.py:
    .process    — running-subprocess registry
    .worktree   — git worktree creation + cleanup
    .scaffold   — TASK.md / CLAUDE.md / .mcp.json authoring
    .kickoff    — `_kickoff_agent_headless`

The high-level orchestrators (`prepare`, `kickoff_coding_task`,
`list_prepared`) live here, plus re-exports of the public API so
existing `from planet_maiko.agents.coding_agent import X` calls
keep working without churn at every call site.
"""

import logging
import os
import subprocess
import uuid
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate

# Re-exports — keep existing call sites working.
from .process import (
    register_running_process,
    unregister_running_process,
    stop_agent_session,
)
from .worktree import (
    _slugify,
    _fetch_latest_base,
    _create_worktree,
    cleanup,
    cleanup_task_worktree,
)
from .scaffold import (
    _write_task_file,
    _write_claude_md,
    _build_playbook_section,
    _build_agent_notes_section,
    _build_specialty_section,
    _inherit_mcp_servers,
    _write_mcp_json,
    _write_claude_settings,
)
from .kickoff import _kickoff_agent_headless

logger = logging.getLogger(__name__)


def prepare(task_id, task_title, prompt, repo_path, branch_prefix="maiko",
            agent_profile_id=None, role="coding", specialty_id=None):
    """Prepare a git worktree on a new branch for an agent to work on a task.

    The agent is not started — caller invokes _kickoff_agent_headless()
    separately when ready.

    Args:
        task_id: the task this agent will work on
        task_title: human-readable task title
        prompt: full instructions for the agent
        repo_path: path to the git repository
        branch_prefix: prefix for the branch name
        role: "coding" | "review" | "investigation" | "cartographer" —
            picks the CLAUDE.md protocol template and role-scoped team
            instructions.
        specialty_id: optional CustomSkill id. If set and attached to
            the agent, its prompt is layered onto CLAUDE.md as the
            "specialty for this run" section. None = base role only.

    Returns:
        dict with agent info and launch instructions, or None on failure
    """
    # Build a descriptive branch name from the task title.
    # Suffix = last 5 digits of unix time + 4 hex chars of uuid. The
    # short timestamp keeps branch names skim-readable; the uuid chars
    # make collisions effectively impossible even when two tasks land
    # on the same second with the same title (which used to silently
    # land both agents on the same branch via a 4-digit timestamp).
    import time as _time
    slug = _slugify(task_title, max_len=40)
    if not slug:
        slug = _slugify(task_id)
    slug = f"{slug}-{str(int(_time.time()))[-5:]}-{uuid.uuid4().hex[:4]}"

    # If the user typed a full branch name (contains /), use it as-is.
    # Otherwise treat it as a prefix and append the auto-generated slug.
    if "/" in branch_prefix:
        branch_name = branch_prefix
    else:
        if branch_prefix == "maiko":
            try:
                from planet_maiko.config import load_config
                cfg_prefix = load_config().get("agents", {}).get("branch_prefix", "maiko")
                if cfg_prefix:
                    branch_prefix = cfg_prefix
            except Exception:
                pass
        branch_name = f"{branch_prefix}/{slug}"

    working_path = _create_worktree(repo_path, branch_name)
    if not working_path:
        return None

    # Write task files
    _write_task_file(working_path, task_id, task_title, prompt)
    _write_claude_md(
        working_path, task_id, task_title,
        role=role, parent_repo_path=repo_path,
        agent_profile_id=agent_profile_id,
        specialty_id=specialty_id,
    )
    # Pass repo_path so the worktree's .mcp.json inherits the user's
    # per-project MCPs (Linear / Slack / etc.) — otherwise the agent
    # session only has maiko-channel and feels MCP-blind compared to
    # the user's normal Claude Code session in the parent repo.
    _write_mcp_json(working_path, task_id, parent_repo_path=repo_path)

    # Use existing profile if provided, otherwise generate an ID
    agent_id = agent_profile_id or f"agent-{branch_name}"

    # Write Claude Code hooks configuration
    _write_claude_settings(working_path, task_id, agent_id)

    # LoRA compliance review is now handled by Claude Code PostToolUse hook
    # (lora_review_hook.py), registered in _write_claude_settings above.

    # Compile learning brief for this agent
    try:
        from planet_maiko.brain.learning.processor import compile_brief
        from planet_maiko.agents.profiles import create_profile

        # Only create a profile if one wasn't provided
        if not agent_profile_id:
            create_profile(agent_id)
        brief = compile_brief(
            repo=repo_path,
            task_id=task_id,
            agent_profile_id=agent_id,
        )

        if brief and brief != "No active learnings yet.":
            claude_path = os.path.join(working_path, "CLAUDE.md")
            with open(claude_path, "a") as f:
                f.write("\n\n" + brief)
            logger.info(f"[agent] Injected learning brief ({len(brief)} chars) for {agent_id}")
    except Exception as e:
        logger.warning(f"[agent] Could not compile brief for {agent_id}: {e}")

    # Review/investigation agents run autonomously after prepare, so the
    # "ready to launch" framing doesn't fit — their action is "dig deeper"
    # once the result pupdate arrives. Coding agents still need a manual
    # terminal launch.
    if role == "coding":
        ready_title = f"Agent ready: {task_title}"
        ready_hint = "Launch agent"
    else:
        ready_title = f"{role.capitalize()} agent working: {task_title}"
        ready_hint = "Dig deeper"
    notify = Pupdate(
        id=f"agent-ready-{task_id}-{uuid.uuid4().hex[:8]}",
        source="maiko",
        source_id=f"agent/{agent_id}",
        type="agent_ready",
        priority="normal",
        title=ready_title,
        body=f"Prepared on branch `{branch_name}`.\n\nWorking in: {working_path}",
        actionable=True,
        action_hint=ready_hint,
        tags=[task_id, "agent", role],
        extra={
            "agent_id": agent_id,
            "branch": branch_name,
            "working_path": working_path,
            "task_id": task_id,
            "role": role,
        },
    )
    db.session.add(notify)
    db.session.commit()

    logger.info(f"[orchestrator] Prepared agent {agent_id} at {working_path}")

    return {
        "agent_id": agent_id,
        "task_id": task_id,
        "branch": branch_name,
        "working_path": working_path,
        "status": "ready",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "launch_instructions": {
            "claude_code": f'cd {working_path} && claude "Read TASK.md and CLAUDE.md. Begin working on the task."',
            "manual": f"cd {working_path} && cat TASK.md",
        },
    }


def kickoff_coding_task(task, *, plan_first=False, branch_name=None):
    """Prepare a worktree and fire the headless agent for a coding task.

    Resolves repo_path from the task's scope, builds a rich prompt (task
    title + source pupdate body + project description + task detail),
    sets up the worktree via `prepare()`, then calls
    `_kickoff_agent_headless()`. Stores working_path + branch on
    task.extra so the UI can tell a task has been launched and the
    "Launch" button can hide itself.

    Returns a dict: {success: bool, error?: str, working_path?: str,
    branch?: str, kickoff?: dict}. Never raises — callers get a stable
    shape so batch callers (approve_plan) don't have to wrap each call
    in try/except.
    """
    from planet_maiko.database import db
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.project import Project
    from planet_maiko.orchestration import resolve_repo_path, scope_for_task
    from planet_maiko.config import load_config

    if not task.assigned_agent_id:
        return {"success": False, "error": "No agent assigned"}
    agent = db.session.get(AgentProfile, task.assigned_agent_id)
    if not agent:
        return {"success": False, "error": "Assigned agent no longer exists"}
    if agent.role != "coding":
        return {"success": False, "error": f"{agent.role} agents run autonomously — no manual launch"}

    repo = scope_for_task(task)
    repo_path = (task.extra or {}).get("repo_path") or resolve_repo_path(repo)
    if not repo_path:
        return {"success": False, "error": f"No local clone found for {repo or 'this task'}. Set agents.repo_roots in Settings."}
    if not os.path.isdir(repo_path):
        return {"success": False, "error": f"Repository path not found: {repo_path}"}
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return {"success": False, "error": f"Not a git repository: {repo_path}"}

    prompt_parts = [task.title]
    if task.source_pupdate_id:
        source = db.session.get(Pupdate, task.source_pupdate_id)
        if source and source.body:
            prompt_parts.append(f"\n## Source Context\n\n{source.body}")
        if source and source.url:
            prompt_parts.append(f"\nSource URL: {source.url}")
    if task.project_id:
        project = db.session.get(Project, task.project_id)
        if project and project.description:
            prompt_parts.append(f"\n## Project: {project.title}\n\n{project.description}")
    extra_desc = (task.extra or {}).get("description")
    if extra_desc:
        prompt_parts.append(f"\n## Task details\n\n{extra_desc}")
    if task.url:
        prompt_parts.append(f"\nTask URL: {task.url}")
    if task.tags:
        prompt_parts.append(f"\nTags: {', '.join(task.tags)}")
    full_prompt = "\n".join(prompt_parts)

    branch_prefix = branch_name or (load_config().get("agents", {}) or {}).get("branch_prefix", "maiko")

    try:
        prep_result = prepare(
            task_id=task.id,
            task_title=task.title,
            prompt=full_prompt,
            repo_path=repo_path,
            branch_prefix=branch_prefix,
            agent_profile_id=agent.id,
            role="coding",
        )
    except Exception as e:
        return {"success": False, "error": f"Prepare failed: {e}"}
    if not prep_result:
        return {"success": False, "error": "Prepare failed"}

    branch = prep_result.get("branch")
    working_path = prep_result.get("working_path")

    kickoff = _kickoff_agent_headless(
        agent.id, working_path, task.id,
        plan_first=plan_first,
    )

    new_extra = dict(task.extra or {})
    new_extra["working_path"] = working_path
    new_extra["branch"] = branch
    if plan_first:
        new_extra["plan_first"] = True
    task.extra = new_extra
    db.session.commit()

    return {
        "success": True,
        "working_path": working_path,
        "branch": branch,
        "kickoff": kickoff,
    }


def list_prepared():
    """List prepared agent worktrees whose task is still open.

    Filters out agent_ready pupdates that are dismissed, that
    reference a task in done/cancelled state, or that reference a
    task that no longer exists. Those are finished work, not active
    work — leaving them on the Active tab buries the real signal.
    """
    from planet_maiko.models.task import Task
    agents = Pupdate.query.filter_by(type="agent_ready", dismissed=False).all()
    task_ids = [p.extra.get("task_id") for p in agents if p.extra.get("task_id")]
    tasks_by_id = {}
    if task_ids:
        tasks_by_id = {t.id: t for t in Task.query.filter(Task.id.in_(task_ids)).all()}
    out = []
    for p in agents:
        tid = p.extra.get("task_id")
        task = tasks_by_id.get(tid)
        if tid and not task:
            continue  # task deleted
        if task and task.status in ("done", "cancelled"):
            continue  # finished
        out.append({
            "agent_id": p.extra.get("agent_id"),
            "task_id": tid,
            "task_title": task.title if task else None,
            "task_status": task.status if task else None,
            "role": p.extra.get("role", "coding"),
            "branch": p.extra.get("branch"),
            "working_path": p.extra.get("working_path"),
            "prepared_at": p.timestamp.isoformat() if p.timestamp else None,
        })
    return out
