"""Pack dispatcher — turn a natural-language ask into a routed task.

The user types something like "can you see what's going on with the
deploys" into the Ask the Pack box. We:

  1. Show the LLM the available agents + known repos.
  2. Let it pick an agent (or declare we need a new specialist) and
     draft a task body.
  3. Create the task, assign it, and — for one-shot roles — kick the
     agent off in the background so they're on the case by the time
     the user finishes reading the confirmation.

Coding tasks are created assigned-but-not-launched; the user still
needs to confirm a repo and hit Launch. Everything else runs
autonomously.

The routing prompt lives in `src/planet_maiko/prompts/pack-router.md`.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone

from planet_maiko.database import db

logger = logging.getLogger(__name__)


_VALID_ROLES = {"coding", "review", "investigation", "cartographer"}
_VALID_TYPES = {"review", "pr_review", "investigation", "repo_analysis", "cartograph", "todo"}
_VALID_PRIORITIES = {"urgent", "high", "normal", "low"}


def dispatch(request: str, context: str = "") -> dict:
    """Route a natural-language ask to an agent and launch them.

    Args:
        request: The user's ask, in plain English.
        context: Optional extra context from the user (URL, file path,
            "this is blocking release", etc.). Concatenated into the
            prompt as `context`.

    Returns:
        A dict shaped:

            {
              "status": "dispatched" | "clarify" | "error",
              "agent": {...profile dict...},          # when dispatched
              "task": {...task dict...},               # when dispatched
              "message": "Yoshi is on it — ...",       # when dispatched
              "reasoning": "...",                      # when dispatched
              "launch_status": "kicked_off" | "queued",
              "clarify": "...question...",             # when clarify
              "error": "...",                          # when error
            }
    """
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model
    from planet_maiko.agents.skills import get_skill_prompt
    from planet_maiko.models.agent_profile import AgentProfile

    req = (request or "").strip()
    if not req:
        return {"status": "error", "error": "request is required"}

    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        return {"status": "error", "error": "LLM runtime not available. Is Claude Code installed?"}

    profiles = (
        AgentProfile.query
        .filter((AgentProfile.archived == False) | (AgentProfile.archived == None))  # noqa: E712
        .order_by(AgentProfile.tasks_completed.desc())
        .all()
    )
    agents_payload = [_profile_for_router(p) for p in profiles]
    repos_payload = _known_repos()

    prompt = get_skill_prompt("pack-router", {
        "query": req,
        "context": (context or "").strip() or "(none provided)",
        "agents": json.dumps(agents_payload, indent=2) if agents_payload else "[]",
        "repos": json.dumps(repos_payload) if repos_payload else "[]",
    })
    if not prompt:
        return {"status": "error", "error": "pack-router prompt not found"}

    db.session.close()

    result = runtime.send_json(prompt, timeout=45, model=resolve_model("triage"))
    if not result.get("success"):
        return {"status": "error", "error": result.get("error", "router call failed")}

    decision = result.get("parsed")
    if not isinstance(decision, dict):
        return {"status": "error", "error": "router did not return valid JSON"}

    clarify = (decision.get("clarify") or "").strip()
    if clarify:
        return {"status": "clarify", "clarify": clarify}

    role = (decision.get("role") or "").strip()
    if role not in _VALID_ROLES:
        return {"status": "error", "error": f"router picked invalid role: {role!r}"}

    task_type = (decision.get("type") or "").strip() or "todo"
    if task_type not in _VALID_TYPES:
        return {"status": "error", "error": f"router picked invalid type: {task_type!r}"}

    title = (decision.get("title") or "").strip()
    if not title:
        return {"status": "error", "error": "router did not produce a title"}
    title = title[:200]

    description = (decision.get("description") or "").strip()
    priority = decision.get("priority") or "normal"
    if priority not in _VALID_PRIORITIES:
        priority = "normal"
    scope_repo = (decision.get("scope_repo") or None) or None
    reasoning = (decision.get("reasoning") or "").strip()
    preferred_id = (decision.get("preferred_profile_id") or "").strip() or None

    profile = _resolve_profile(preferred_id, role, scope_repo)
    if not profile:
        return {"status": "error", "error": "could not resolve or spawn an agent"}

    task = _create_dispatched_task(
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        scope_repo=scope_repo,
        context=context,
        profile=profile,
        user_request=req,
    )

    launch_status = "queued"
    if task_type in ("review", "pr_review", "investigation", "repo_analysis", "cartograph"):
        launch_status = _launch_one_shot(task.id, role)

    message = _friendly_confirmation(profile, task, role, launch_status, reasoning)

    return {
        "status": "dispatched",
        "agent": profile.to_dict(),
        "task": task.to_dict(),
        "message": message,
        "reasoning": reasoning,
        "launch_status": launch_status,
    }


# ---------------------------------------------------------------------------
# Router input helpers
# ---------------------------------------------------------------------------

def _profile_for_router(p) -> dict:
    """Small projection of an AgentProfile — just what the router
    needs to pick between candidates. Keeps the prompt short."""
    return {
        "id": p.id,
        "display_name": p.display_name,
        "role": p.role or "coding",
        "scope_repo": p.scope_repo,
        "tasks_completed": p.tasks_completed or 0,
        "flavor_text": (p.flavor_text or "")[:120],
    }


def _known_repos() -> list[str]:
    """List repos the pack knows about, gathered from recent tasks +
    existing profiles + any GitHub-configured repos. De-duped."""
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile

    seen = set()
    out = []

    def _add(repo):
        if repo and repo not in seen:
            seen.add(repo)
            out.append(repo)

    for p in AgentProfile.query.filter(AgentProfile.scope_repo.isnot(None)).all():
        _add(p.scope_repo)

    recent = Task.query.order_by(Task.updated_at.desc()).limit(40).all()
    for t in recent:
        extra = t.extra or {}
        _add(extra.get("repo") or extra.get("repository"))

    return out


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------

def _resolve_profile(preferred_id, role, scope_repo):
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.orchestration import find_profile, maybe_spawn

    if preferred_id:
        existing = db.session.get(AgentProfile, preferred_id)
        if existing and not existing.archived:
            return existing
        logger.info(f"[pack] router picked profile {preferred_id!r} but it's missing/archived; falling back to role lookup")

    profile = find_profile(role, scope_repo)
    if profile:
        return profile
    return maybe_spawn(role, scope_repo)


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------

def _create_dispatched_task(*, title, description, task_type, priority, scope_repo, context, profile, user_request):
    """Create a Task row, stamp it with pack-dispatcher metadata, and
    assign the picked profile. The caller is responsible for launching
    (or not)."""
    from planet_maiko.models.task import Task

    task_id = f"pack-{uuid.uuid4().hex[:10]}"
    body = description or user_request
    extra = {
        "description": body,
        "source": "pack",
        "user_request": user_request,
    }
    if context and context.strip():
        extra["user_context"] = context.strip()
    if scope_repo:
        extra["repo"] = scope_repo

    task = Task(
        id=task_id,
        title=title,
        type=task_type,
        status="new",
        priority=priority,
        assigned_agent_id=profile.id,
        tags=["pack"],
        extra=extra,
    )
    db.session.add(task)
    db.session.commit()
    return task


# ---------------------------------------------------------------------------
# Launching
# ---------------------------------------------------------------------------

def _launch_one_shot(task_id, role):
    """Prepare a worktree + fire the agent in a background thread so
    the HTTP response doesn't block on claude startup. Mirrors what
    /agents/assign does for review/investigation/cartographer roles.

    Returns "kicked_off" if we started the thread, "queued" if we
    couldn't (no repo clone on disk, etc.) — the task still exists and
    the user can retry from the Agents tab.
    """
    from flask import current_app
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import resolve_repo_path, scope_for_task

    task = db.session.get(Task, task_id)
    if not task:
        return "queued"

    repo = scope_for_task(task)
    local_path = resolve_repo_path(repo)
    if not local_path:
        # No clone found locally — leave the task in "new" so the user
        # can reassign / point at a clone from the Agents tab. The
        # assigned agent is still on it, just not running yet.
        logger.info(f"[pack] queued {task_id} — no local clone for {repo!r}")
        return "queued"

    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            try:
                from planet_maiko.agents.coding_agent import prepare, _kickoff_agent_headless
                from planet_maiko.api.agents_api import _build_task_prompt
                t = db.session.get(Task, task_id)
                if not t:
                    return
                full_prompt = _build_task_prompt(t, role)
                result = prepare(
                    task_id=t.id,
                    task_title=t.title,
                    prompt=full_prompt,
                    repo_path=local_path,
                    branch_prefix="maiko",
                    auto_kickoff=False,
                    use_worktree=True,
                    agent_profile_id=t.assigned_agent_id,
                    role=role,
                )
                if not result:
                    logger.warning(f"[pack] worktree prep failed for {task_id}")
                    return
                working_path = result.get("working_path")
                branch = result.get("branch")
                _kickoff_agent_headless(
                    t.assigned_agent_id, working_path, t.id,
                    branch_name=None, plan_first=False, role=role,
                )
                extra = dict(t.extra or {})
                if working_path:
                    extra["working_path"] = working_path
                if branch:
                    extra["branch"] = branch
                t.extra = extra
                if t.status == "new":
                    t.status = "in_progress"
                t.updated_at = datetime.now(timezone.utc)
                db.session.commit()
            except Exception as e:
                logger.exception(f"[pack] dispatch launch failed for {task_id}: {e}")

    threading.Thread(target=_run, daemon=True, name=f"pack-dispatch-{task_id}").start()
    return "kicked_off"


# ---------------------------------------------------------------------------
# UI-facing confirmation line
# ---------------------------------------------------------------------------

def _friendly_confirmation(profile, task, role, launch_status, reasoning):
    """The one-line "on it" message shown under the input. Warm,
    specific, names the agent. If the task needs a manual launch,
    that's said out loud so the user knows what to do next."""
    name = profile.display_name or "The pack"

    if role == "coding":
        head = f"{name} has this queued — hit Launch on the task when you're ready."
    elif launch_status == "kicked_off":
        head = f"{name} is on it."
    else:
        head = f"{name} has this, but needs a local clone before they can start."

    if reasoning and reasoning.lower() not in head.lower():
        return f"{head} {reasoning}"
    return head
