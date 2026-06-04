"""Agent runtime. Worktree + kickoff machinery for every role.

Every role goes through the same prepare to kickoff to AgentJob
flow: coding, review, investigation, cartographer, and specialty
agents alike.

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

Submodules:
    .process    — running-subprocess registry
    .worktree   — git worktree creation + cleanup
    .scaffold   — TASK.md / CLAUDE.md / .mcp.json authoring
    .kickoff    — `_kickoff_agent_headless`

The high-level orchestrators (`prepare`, `list_prepared`) live here,
plus re-exports of the public API so call sites can do
`from planet_maiko.agents.runtime import X`.
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
    _create_scratch_dir,
    cleanup,
    cleanup_task_worktree,
    sweep_old_worktrees,
    worktree_stats,
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


def prepare(job_id, job_title, prompt, repo_path, branch_prefix="maiko",
            agent_profile_id=None, role="coding", specialty_id=None,
            pr_number=None, base_branch=None):
    """Prepare a working directory for an agent to work on a job.

    `job_id` is an AgentJob.id. Every agent's MAIKO_JOB_ID is its
    AgentJob.id.

    Two flavors, picked automatically by whether `repo_path` is set:

    - Repo-backed (the default): mints a fresh git worktree on a new
      branch cut from the latest origin/<default> tip (or the PR's
      head ref when `pr_number` is set). What coding / review / PR-
      review agents need.

    - Scratch (when `repo_path` is falsy): mints a plain working dir
      under <data_dir>/scratch-worktrees/<job_id>. No git, no branch.
      For planning skills, investigation, and one-off question
      answerers — agents that don't touch code and shouldn't force
      the user to pick a repo just to run.

    The agent is not started — caller invokes _kickoff_agent_headless()
    separately when ready.

    Args:
        job_id: the AgentJob this agent will work on
        job_title: human-readable job title
        prompt: full instructions for the agent
        repo_path: path to the git repository, or None/"" for scratch
        branch_prefix: prefix for the branch name (ignored in scratch)
        role: "coding" | "review" | "investigation" | "cartographer" —
            picks the CLAUDE.md protocol template and role-scoped team
            instructions.
        specialty_id: optional CustomSkill id. If set and attached to
            the agent, its prompt is layered onto CLAUDE.md as the
            "specialty for this run" section. None = base role only.

    Returns:
        dict with agent info and launch instructions, or None on failure.
        Scratch runs return branch=None and working_path under the
        scratch root.
    """
    scratch_mode = not repo_path

    if scratch_mode:
        # No repo, no branch. Working dir is keyed by AgentJob.id,
        # which is already unique — no collision dance needed.
        branch_name = None
        working_path = _create_scratch_dir(job_id)
        if not working_path:
            return None
    else:
        # Build a descriptive branch name from the job title.
        # Suffix = last 5 digits of unix time + 4 hex chars of uuid. The
        # short timestamp keeps branch names skim-readable; the uuid
        # chars make collisions effectively impossible even when two
        # jobs land on the same second with the same title.
        import time as _time
        slug = _slugify(job_title, max_len=40)
        if not slug:
            slug = _slugify(job_id)
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

        # Review jobs pass pr_number so the worktree is built from the PR's
        # head ref instead of origin/main — the agent needs to see the
        # actual code under review for `git diff origin/main...HEAD` and
        # leave_comment to pin to real lines. A stacked child passes
        # base_branch so its worktree is cut from the parent task's branch.
        working_path = _create_worktree(
            repo_path, branch_name, pr_number=pr_number, base_branch=base_branch,
        )
        if not working_path:
            return None

    # Write the agent's bootstrap files into the working dir.
    _write_task_file(working_path, job_id, job_title, prompt)
    _write_claude_md(
        working_path, job_id, job_title,
        role=role, parent_repo_path=repo_path,
        agent_profile_id=agent_profile_id,
        specialty_id=specialty_id,
    )
    # Pass repo_path so the worktree's .mcp.json inherits the user's
    # per-project MCPs (Linear / Slack / etc.) — otherwise the agent
    # session only has maiko-channel and feels MCP-blind compared to
    # the user's normal Claude Code session in the parent repo.
    _write_mcp_json(working_path, job_id, parent_repo_path=repo_path)

    # Use existing profile if provided, otherwise generate an ID. In
    # scratch mode there's no branch to key off, so we fall back to the
    # job_id which is already unique.
    if agent_profile_id:
        agent_id = agent_profile_id
    elif branch_name:
        agent_id = f"agent-{branch_name}"
    else:
        agent_id = f"agent-{job_id}"

    # Write Claude Code hooks configuration
    _write_claude_settings(working_path, job_id, agent_id)

    # LoRA verifier is currently parked — the training pipeline + inference
    # modules stay in brain/learning/ but no Claude Code hooks fire it and
    # the agent-facing tools are removed. Code review feedback continues
    # to flow via live RAG (rules-relevant) below.

    # Every role learns rules the same way: live RAG via `maiko
    # rules-relevant`, queried at planning + pre-ready_for_review per
    # the agent-protocol prompts. The previous static top-15 brief
    # injected into CLAUDE.md doubled up with retrieval, biased agents
    # toward popularity over relevance, and risked them leaning on a
    # stale snapshot instead of the live retrieval the protocol asks
    # for. Profile creation still happens here.
    try:
        from planet_maiko.agents.profiles import create_profile
        if not agent_profile_id:
            create_profile(agent_id)
    except Exception as e:
        logger.warning(f"[agent] Could not create profile for {agent_id}: {e}")

    # Review/investigation agents run autonomously after prepare, so the
    # "ready to launch" framing doesn't fit — their action is "dig deeper"
    # once the result pupdate arrives. Coding agents still need a manual
    # terminal launch.
    if role == "coding":
        ready_title = f"Agent ready: {job_title}"
        ready_hint = "Launch agent"
    else:
        ready_title = f"{role.capitalize()} agent working: {job_title}"
        ready_hint = "Dig deeper"
    if scratch_mode:
        body_text = f"Prepared in scratch workspace.\n\nWorking in: {working_path}"
    else:
        body_text = f"Prepared on branch `{branch_name}`.\n\nWorking in: {working_path}"
    notify = Pupdate(
        id=f"agent-ready-{job_id}-{uuid.uuid4().hex[:8]}",
        source="maiko",
        source_id=f"agent/{agent_id}",
        type="agent_ready",
        priority="normal",
        title=ready_title,
        body=body_text,
        actionable=True,
        action_hint=ready_hint,
        tags=[job_id, "agent", role],
        extra={
            "agent_id": agent_id,
            "branch": branch_name,
            "working_path": working_path,
            "job_id": job_id,
            "role": role,
            "scratch": scratch_mode,
        },
    )
    db.session.add(notify)
    db.session.commit()

    logger.info(f"[orchestrator] Prepared agent {agent_id} at {working_path}")

    return {
        "agent_id": agent_id,
        "job_id": job_id,
        "branch": branch_name,
        "working_path": working_path,
        "status": "ready",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "launch_instructions": {
            "claude_code": f'cd {working_path} && claude "Read TASK.md and CLAUDE.md. Begin working on the task."',
            "manual": f"cd {working_path} && cat TASK.md",
        },
    }


def list_prepared():
    """List prepared agent worktrees whose underlying job is still open.

    Filters out agent_ready pupdates that are dismissed, that point at
    a job in done/cancelled/failed state, or whose job doesn't exist
    anymore. Those are finished work, not active work; leaving them
    on the Active tab buries the real signal.

    Reads `extra.job_id` with a fallback to `extra.task_id` for older
    agent_ready rows. Looks up against AgentJob.
    """
    from planet_maiko.models.agent_job import AgentJob
    agents = Pupdate.query.filter_by(type="agent_ready", dismissed=False).all()
    job_ids = []
    for p in agents:
        ex = p.extra or {}
        jid = ex.get("job_id") or ex.get("task_id")
        if jid:
            job_ids.append(jid)
    jobs_by_id = {}
    if job_ids:
        jobs_by_id = {
            j.id: j for j in AgentJob.query.filter(AgentJob.id.in_(job_ids)).all()
        }
    out = []
    for p in agents:
        ex = p.extra or {}
        jid = ex.get("job_id") or ex.get("task_id")
        job = jobs_by_id.get(jid)
        if jid and not job:
            continue  # job deleted
        if job and job.status in ("done", "cancelled", "failed"):
            continue  # finished
        out.append({
            "agent_id": ex.get("agent_id"),
            "job_id": jid,
            "job_title": job.title if job else None,
            "job_status": job.status if job else None,
            "role": ex.get("role", "coding"),
            "branch": ex.get("branch"),
            "working_path": ex.get("working_path"),
            "prepared_at": p.timestamp.isoformat() if p.timestamp else None,
        })
    return out
