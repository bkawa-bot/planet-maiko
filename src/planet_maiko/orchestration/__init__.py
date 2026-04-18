"""Agent orchestration: map signals/tasks to agent profiles.

Agent profiles are *configurations* (role + scope_repo + LoRA adapter +
instructions + persona), not sessions. Multiple tasks can be assigned
to the same profile simultaneously — each task spawns its own session
that uses that profile's config.

Routing is a simple lookup on (role, scope_repo). If no matching
profile exists, one is lazy-spawned. Load balancing, success rate,
etc. are deliberately NOT part of routing — those are session-level
concerns.
"""

import logging
import uuid
from typing import Iterable, Optional

from planet_maiko.database import db
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)


def build_task_prompt(task, role, custom_prompt=""):
    """Compose the TASK.md body for a task about to be handed to an agent.

    Lives here (not in `api/agents_api.py`) because three different
    code paths hand-build TASK.md today: the assign API, the pack
    dispatcher, and the brain cycle's safety-net one-shot executor.
    When the logic was only in agents_api.py the cycle's inline copy
    had drifted — it was missing the Description and Source Context
    sections, so tasks executed via the cycle's retry path arrived
    with less context than the same task executed via the API.

    Base context for every role: title + description + source
    pupdate + project + URL + tags. For review / investigation we
    additionally embed the skill prompt so the agent has the full
    recipe in TASK.md and can use the unified _kickoff_agent_headless
    path without a separate skill-runner.
    """
    from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.project import Project as _Project

    parts = [task.title]

    # The task description — where the UI task form writes what the
    # user typed, where the plan generator writes its description
    # field, and where Linear writes the issue body. The earlier
    # version of this helper never read it, so manually-created
    # tasks arrived at the agent with just a title and the agent
    # confabulated everything else.
    extra = task.extra or {}
    description = extra.get("description") or extra.get("body")
    if description:
        parts.append(f"\n## Description\n\n{description}")

    if task.source_pupdate_id:
        source = db.session.get(Pupdate, task.source_pupdate_id)
        if source and source.body:
            parts.append(f"\n## Source Context\n\n{source.body}")
        if source and source.url:
            parts.append(f"\nSource URL: {source.url}")

    if task.project_id:
        project = db.session.get(_Project, task.project_id)
        if project and project.description:
            parts.append(f"\n## Project: {project.title}\n\n{project.description}")

    if task.url:
        parts.append(f"\nTask URL: {task.url}")
    if task.tags:
        parts.append(f"\nTags: {', '.join(task.tags)}")

    if role in ("review", "investigation"):
        try:
            from planet_maiko.agents.skills import get_skill_prompt
            skill_name = ONE_SHOT_ROLE_FOR_TYPE.get(task.type, (None, None))[1]
            if skill_name:
                context = {
                    "query": task.title,
                    "context": f"URL: {task.url or ''}\nRepo: {(task.extra or {}).get('repo', '')}",
                    "pupdates": "[]", "tasks": "[]", "calendar": "[]",
                }
                skill_prompt = get_skill_prompt(skill_name, context) or ""
                if skill_prompt.strip():
                    parts.append(f"\n## Skill: {skill_name}\n\n{skill_prompt}")
        except Exception as e:
            logger.warning(f"[orchestration] Could not embed skill prompt for task {task.id}: {e}")

    if custom_prompt:
        parts.append(f"\n## Additional Instructions\n\n{custom_prompt}")

    return "\n".join(parts)

# Task.type → required agent role. Anything not in this map defaults to
# "coding" — the historical behavior.
TYPE_TO_ROLE = {
    "review": "review",
    "pr_review": "review",
    "investigation": "investigation",
    "repo_analysis": "investigation",
}


def role_for_task(task: Task) -> str:
    return TYPE_TO_ROLE.get(task.type or "", "coding")


def scope_for_task(task: Task) -> Optional[str]:
    """The repo this task belongs to, if any. None = global/no-scope.

    Falls back to parsing task.url when task.extra doesn't carry a
    repo key — catches tasks created by older pollers or by hand
    whose URL still points at a github.com PR / issue.
    """
    extra = task.extra or {}
    scope = extra.get("repo") or extra.get("repository")
    if scope:
        return scope
    return _repo_from_github_url(task.url)


def _repo_from_github_url(url: Optional[str]) -> Optional[str]:
    """Extract "org/repo" from a github.com URL. Returns None if the
    URL isn't a recognizable github link.
    """
    if not url or "github.com/" not in url:
        return None
    try:
        tail = url.split("github.com/", 1)[1]
        parts = tail.split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass
    return None


def resolve_repo_path(repo: Optional[str]) -> Optional[str]:
    """Resolve a GitHub-style "org/repo" to a local filesystem path.

    Walks config.github.repo_roots (set under Settings) looking for a
    directory that is a git repo and whose name matches the last
    segment of `repo` — i.e. the repo name without the org prefix.
    Most devs clone flat (~/src/planet-maiko, not
    ~/src/bkawa-bot/planet-maiko), so we only check <root>/<name>.
    """
    if not repo:
        return None
    import os
    from planet_maiko.config import load_config
    roots = (load_config().get("github", {}) or {}).get("repo_roots") or []
    name = repo.rsplit("/", 1)[-1]
    for root in roots:
        root = os.path.expanduser(root)
        candidate = os.path.join(root, name)
        if os.path.isdir(os.path.join(candidate, ".git")):
            return candidate
    return None


def find_profile(role: str, scope_repo: Optional[str]) -> Optional[AgentProfile]:
    """Exact (role, scope_repo) match. None if no profile fits."""
    q = AgentProfile.query.filter(
        AgentProfile.role == role,
        (AgentProfile.archived == False) | (AgentProfile.archived == None),  # noqa: E712
    )
    if scope_repo is None:
        q = q.filter(AgentProfile.scope_repo.is_(None))
    else:
        q = q.filter(AgentProfile.scope_repo == scope_repo)
    return q.first()


def maybe_spawn(role: str, scope_repo: Optional[str]) -> AgentProfile:
    """Find or create a profile for (role, scope_repo)."""
    existing = find_profile(role, scope_repo)
    if existing:
        return existing

    from planet_maiko.agents.profiles import create_profile
    # Agent IDs for auto-spawned profiles follow a stable, readable pattern
    # so it's obvious in logs which profile handles which slice of work.
    slug = scope_repo.replace("/", "_") if scope_repo else "global"
    agent_id = f"agent-{role}-{slug}-{uuid.uuid4().hex[:6]}"
    profile = create_profile(
        agent_id=agent_id,
        role=role,
        scope_repo=scope_repo,
    )
    logger.info(f"[orchestration] Spawned {role} agent {profile.display_name} for scope={scope_repo}")
    return profile


def route(task: Task) -> str:
    """Assign an agent to a task. Returns the agent_id written onto the task.

    Idempotent — if task.assigned_agent_id is already set to a still-alive
    profile, it's returned unchanged.
    """
    if task.assigned_agent_id:
        existing = db.session.get(AgentProfile, task.assigned_agent_id)
        if existing and not existing.archived:
            return task.assigned_agent_id

    role = role_for_task(task)
    scope_repo = scope_for_task(task)
    profile = maybe_spawn(role, scope_repo)
    task.assigned_agent_id = profile.id
    return profile.id


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def is_ready(task: Task) -> bool:
    """True when all of task.depends_on are status=done."""
    deps = task.depends_on or []
    if not deps:
        return True
    unfinished = Task.query.filter(
        Task.id.in_(deps),
        Task.status != "done",
    ).count()
    return unfinished == 0


def has_cycle(task_id: str, depends_on: Iterable[str]) -> bool:
    """Check whether adding `depends_on` to `task_id` would create a cycle.

    Walks the existing dep graph from each target. If we ever visit
    `task_id`, adding the edge would form a cycle.
    """
    depends_on = [d for d in depends_on if d and d != task_id]
    if not depends_on:
        return False

    # Pull every task's depends_on once — cheap for any realistic list
    # length and avoids an N+1 query pattern while traversing.
    all_tasks = {t.id: (t.depends_on or []) for t in Task.query.all()}

    def reaches(start, target):
        stack = list(all_tasks.get(start, []))
        seen = set()
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(all_tasks.get(node, []))
        return False

    return any(reaches(dep, task_id) for dep in depends_on)


def compute_initial_status(task: Task) -> str:
    """Return the correct initial status for a freshly-created task.

    "blocked" if it has unfinished deps, otherwise preserve whatever
    status the caller set (defaults to "new").
    """
    if not is_ready(task):
        return "blocked"
    return task.status or "new"
