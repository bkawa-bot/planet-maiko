from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.project import Project

projects_bp = Blueprint("projects", __name__)


@projects_bp.route("/projects", methods=["GET"])
def list_projects():
    """List projects with optional filtering by status."""
    status = request.args.get("status")

    query = Project.query
    if status:
        query = query.filter_by(status=status)

    projects = query.order_by(Project.created_at.desc()).all()
    return jsonify([p.to_dict() for p in projects])


@projects_bp.route("/projects/<project_id>", methods=["GET"])
def get_project(project_id):
    """Get a single project with its tasks."""
    project = db.get_or_404(Project, project_id)
    data = project.to_dict()
    data["tasks"] = [t.to_dict() for t in project.tasks]
    return jsonify(data)


@projects_bp.route("/projects", methods=["POST"])
def create_project():
    """Create a new project."""
    data = request.get_json()
    project = Project(
        id=data["id"],
        title=data["title"],
        description=data.get("description"),
        status=data.get("status", "planning"),
        priority=data.get("priority", "normal"),
        source_type=data.get("source_type"),
        source_id=data.get("source_id"),
        source_url=data.get("source_url"),
        extra=data.get("metadata", {}),
    )
    db.session.add(project)
    db.session.commit()
    return jsonify(project.to_dict()), 201


@projects_bp.route("/projects/<project_id>", methods=["PATCH"])
def update_project(project_id):
    """Update project fields."""
    project = db.get_or_404(Project, project_id)
    data = request.get_json()

    for field in ["title", "description", "priority", "source_type", "source_id", "source_url"]:
        if field in data:
            setattr(project, field, data[field])
    if "metadata" in data:
        project.extra = data["metadata"]

    project.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(project.to_dict())


@projects_bp.route("/projects/<project_id>/status", methods=["POST"])
def update_status(project_id):
    """Update project status (planning, approved, active, paused, done)."""
    project = db.get_or_404(Project, project_id)
    data = request.get_json()

    valid_statuses = {"planning", "approved", "active", "paused", "done"}
    new_status = data.get("status")
    if new_status not in valid_statuses:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}"}), 400

    project.status = new_status
    project.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(project.to_dict())


@projects_bp.route("/projects/<project_id>/generate-plan", methods=["POST"])
def generate_plan(project_id):
    """Use LLM to generate a project plan — scope, approach, risks, phases."""
    project = db.get_or_404(Project, project_id)

    from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
    from planet_maiko.agents.routing import resolve_model

    prompt = f"""Create a concise project plan for this work.

Project: {project.title}
Description: {project.description or "No description provided."}

Write a plan covering:
1. **Summary** — 2-3 sentences on what this project does
2. **Approach** — how to implement it (key technical decisions)
3. **Phases** — break into 2-4 phases (e.g. "Phase 1: Core logic", "Phase 2: Tests & polish")
4. **Risks** — 1-3 things that could go wrong
5. **Scope** — what's in scope and explicitly out of scope

Keep it concise and actionable. Use markdown formatting."""

    # Release DB before long LLM call to avoid SQLite locks
    project_id_saved = project.id
    db.session.close()

    # Plan generation can run long on dense descriptions — 300s matches
    # the runtime default and leaves headroom so we don't bail before
    # Claude finishes thinking through phases/risks.
    runtime = ClaudeCodeRuntime()
    result = runtime.send(prompt, timeout=300, model=resolve_model("project_plan"))

    if not result.get("success") or not result.get("output"):
        return jsonify({"error": result.get("error", "Failed to generate plan")}), 500

    plan = result["output"]

    # Re-fetch project after session was closed, then save plan
    project = db.get_or_404(Project, project_id)
    project.description = plan
    project.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({"plan": plan, "project_id": project_id})


def _preview_agent(role, scope_repo):
    """Return {id, display_name, spawn_new} for what route() would pick.
    Doesn't actually create anything — preview only."""
    from planet_maiko.orchestration import find_profile
    existing = find_profile(role, scope_repo)
    if existing:
        return {"id": existing.id, "display_name": existing.display_name, "spawn_new": False}
    return {"id": None, "display_name": f"(new {role} agent for {scope_repo or 'global'})", "spawn_new": True}


def _collect_project_repo_context(project):
    """Figure out which repos this project touches and where their
    local clones live. Used by /generate-tasks and /revise-tasks so
    the planning LLM can actually grep + read code instead of
    word-associating off the project description.

    Aggregates (in order of trust):
      1. project.extra.repo / project.extra.repos
      2. Each project.phase[*].repo
      3. Each existing task in this project's extra.repo
      4. project.source_url if it parses as github.com/<org>/<repo>

    For each unique repo string, resolves to a local filesystem path
    via orchestration.resolve_repo_path. Returns:
        {
          "primary_path": "<absolute path or None>",
          "repos": [{"name": "org/foo", "local_path": "/path or None"}, ...],
        }

    primary_path is the first repo with a resolvable local path, or
    None if no repo resolved (then the LLM falls back to text-only
    reasoning, same as today).
    """
    from planet_maiko.orchestration import resolve_repo_path
    from planet_maiko.models.task import Task

    seen = []  # preserve insertion order

    def _add(name):
        if name and name not in seen:
            seen.append(name)

    extra = project.extra or {}
    _add(extra.get("repo"))
    for r in (extra.get("repos") or []):
        _add(r)

    for phase in (project.phases or []):
        if isinstance(phase, dict):
            _add(phase.get("repo"))

    project_tasks = Task.query.filter_by(project_id=project.id).all()
    for t in project_tasks:
        te = t.extra or {}
        _add(te.get("repo"))

    src_url = (project.source_url or "")
    if "github.com/" in src_url:
        try:
            tail = src_url.split("github.com/", 1)[1].split("?")[0]
            parts = tail.rstrip("/").split("/")
            if len(parts) >= 2 and parts[0] and parts[1]:
                _add(f"{parts[0]}/{parts[1]}")
        except Exception:
            pass

    repos = [{"name": name, "local_path": resolve_repo_path(name)} for name in seen]
    primary_path = next((r["local_path"] for r in repos if r["local_path"]), None)
    return {"primary_path": primary_path, "repos": repos, "tasks": project_tasks}


def _enrich_draft_tasks(tasks):
    """Sanitize depends_on and attach suggested agent previews.

    Shared by /generate-tasks and /revise-tasks so the draft shape the
    plan editor receives is identical regardless of which flow produced
    it. Mutates each draft in place and also returns the list.
    """
    from planet_maiko.orchestration import TYPE_TO_ROLE
    for idx, draft in enumerate(tasks):
        draft["temp_index"] = idx
        draft.setdefault("depends_on", [])
        draft["depends_on"] = [
            d for d in draft["depends_on"]
            if isinstance(d, int) and 0 <= d < len(tasks) and d != idx
        ]
        role = TYPE_TO_ROLE.get(draft.get("type") or "", "coding")
        scope = draft.get("repo") or None
        draft["suggested_role"] = role
        draft["suggested_scope_repo"] = scope
        draft["suggested_agent"] = _preview_agent(role, scope)
    return tasks


@projects_bp.route("/projects/<project_id>/generate-tasks", methods=["POST"])
def generate_tasks(project_id):
    """Generate a draft task plan with dependencies and proposed assignments.

    Returns an array of draft tasks. Each draft has temp_index (its position
    in the array), title, type, priority, description, repo, category, and
    depends_on (list of indices in the same array). We also pre-compute
    which agent would handle each task via the router so the plan-editor
    UI can surface assignments for review — nothing is persisted as a Task
    yet, nothing is routed. The draft sits in project.extra.generated_tasks
    until the user calls /approve-plan.
    """
    project = db.get_or_404(Project, project_id)

    # Build a real exploration context: which repos does this project
    # touch, where do they live on disk, and what's already in flight.
    # Without this, the LLM is doing pure word association on the
    # project description and produces tasks unmoored from actual code.
    ctx = _collect_project_repo_context(project)
    repos = ctx["repos"]
    primary_path = ctx["primary_path"]
    project_tasks = ctx["tasks"]

    repo_lines = []
    for r in repos:
        loc = r["local_path"] or "(no local clone — add to Settings → GitHub → Repo Roots)"
        repo_lines.append(f"- {r['name']} → {loc}")
    repos_block = "\n".join(repo_lines) if repo_lines else "(no repos identified yet)"

    existing_lines = []
    for t in project_tasks:
        if t.status in ("done", "cancelled"):
            continue
        existing_lines.append(f"- [{t.status}] {t.title} (type={t.type}, repo={(t.extra or {}).get('repo', '')})")
    existing_block = "\n".join(existing_lines) if existing_lines else "(no existing tasks)"

    prompt = f"""You are planning a project. Your job: produce a JSON array of concrete tasks. Before writing them, USE YOUR TOOLS to actually look at the codebase — don't just word-associate off the description.

## Project
{project.title}

## Plan / Description
{project.description or "(no description provided)"}

## Repos in scope
{repos_block}

## Existing open tasks under this project (don't duplicate these)
{existing_block}

## How to plan well

You are running in plan mode (read-only — Read / Glob / Grep / read-only Bash are all available, plus any MCP tools). Before writing tasks:

1. Read the relevant repo's README or root files to understand what kind of project it is.
2. Grep for terms from the project description so the tasks reference real files / modules / functions, not invented ones.
3. Check for existing patterns the new work should follow (existing tests, error handling style, schema conventions).
4. If you have Linear / Slack / GitHub / etc. MCP tools available, use them to pull related issues, recent PRs, or context the description references.

Each task you propose should be specific enough that another agent can pick it up and start working without asking what you meant.

## Output schema

Return ONLY valid JSON — an array of task objects, each with:
- title: concise (≤80 chars)
- type: coding | bug | feature | review | investigation | repo_analysis | todo
- priority: urgent | high | normal | low
- description: 1-2 sentences. Reference real files / functions where possible.
- repo: the repo this task belongs to (one of the repos listed above), or "" if cross-cutting
- category: short tag ("schema", "api", "ui", "test", "docs", "infra", ...)
- depends_on: array of 0-based INDICES of earlier tasks in THIS array that must complete first

Example:
[
  {{"title": "Add sessions table migration", "type": "feature", "priority": "high",
    "description": "Alembic migration in src/auth/migrations/ adding a sessions table with index on user_id (mirrors users table style)", "repo": "org/auth-service",
    "category": "schema", "depends_on": []}},
  {{"title": "Wire /sessions endpoint", "type": "feature", "priority": "normal",
    "description": "New handler in src/auth/api/sessions.py + validation + tests under tests/auth/", "repo": "org/auth-service",
    "category": "api", "depends_on": [0]}}
]

Rules:
- 3-10 tasks total
- depends_on uses indices in THIS array; no cycles; no self-references
- Tasks in different repos with no real dep should NOT be artificially linked
- If a task is purely analysis / discovery, use type "investigation"
- Don't propose tasks that duplicate the existing open tasks above
"""

    # Pre-discover global MCPs so plan mode has access to Linear / Slack /
    # etc. exactly the way an interactive Claude Code session would.
    from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
    runtime = ClaudeCodeRuntime()
    mcp_tools = runtime._discover_global_mcps()

    # Release DB before long LLM call to avoid SQLite locks
    db.session.close()
    # Plan mode = read-only Read / Glob / Grep / restricted Bash, so
    # the LLM can actually explore the repo without being able to
    # mutate it. working_dir points at the primary repo if we have one.
    result = runtime.send_json(
        prompt,
        working_dir=primary_path,
        timeout=300,
        permission_mode="plan" if primary_path else None,
        allowed_tools=mcp_tools or None,
    )

    if not result.get("success") or not result.get("parsed"):
        return jsonify({"error": result.get("error", "Failed to generate tasks")}), 500

    tasks = result["parsed"]
    if not isinstance(tasks, list):
        tasks = [tasks]

    project = db.get_or_404(Project, project_id)
    _enrich_draft_tasks(tasks)

    # Persist the draft on the project so it survives reload.
    meta = dict(project.extra or {}) if project.extra else {}
    meta["generated_tasks"] = tasks
    project.extra = meta
    db.session.commit()

    return jsonify({"tasks": tasks, "project_id": project_id})


@projects_bp.route("/projects/<project_id>/revise-tasks", methods=["POST"])
def revise_tasks(project_id):
    """Revise the current draft task breakdown with user feedback.

    Body: { feedback: str, current_tasks: [...] }. We feed the LLM the
    project plan, the current drafts (including any manual edits the
    user made in the editor), and the freeform feedback, and ask it to
    return a new draft array with the same schema. Manual edits are
    preserved unless the feedback contradicts them. Drop/add/reorder/
    reshape is all fair game.

    The returned draft replaces project.extra.generated_tasks so a
    page reload shows the revised version.
    """
    import json as _json

    project = db.get_or_404(Project, project_id)
    data = request.get_json(silent=True) or {}
    feedback = (data.get("feedback") or "").strip()
    current = data.get("current_tasks")

    if not feedback:
        return jsonify({"error": "feedback is required"}), 400
    if not isinstance(current, list) or not current:
        return jsonify({"error": "current_tasks array is required"}), 400

    # Strip enrichment fields — they're preview-only and shouldn't
    # confuse the LLM into thinking they're part of the schema.
    SCHEMA_KEYS = {
        "title", "type", "priority", "description",
        "repo", "category", "depends_on", "plan_first",
    }
    current_clean = [
        {k: v for k, v in draft.items() if k in SCHEMA_KEYS}
        for draft in current
    ]

    # Same exploration context as generate-tasks — the revise pass
    # often introduces new tasks ("add a testing phase") that benefit
    # from grounding in actual code, not just the description.
    ctx = _collect_project_repo_context(project)
    repos = ctx["repos"]
    primary_path = ctx["primary_path"]
    repo_lines = [
        f"- {r['name']} → {r['local_path'] or '(no local clone)'}" for r in repos
    ]
    repos_block = "\n".join(repo_lines) if repo_lines else "(no repos identified yet)"

    prompt = f"""Revise this draft task plan based on the user's feedback. You can use Read / Glob / Grep / read-only Bash / MCP tools to ground new or changed tasks in real code. Return ONLY valid JSON — an array of task objects.

## Project
{project.title}

## Plan / Description
{project.description or "(no description provided)"}

## Repos in scope
{repos_block}

## Current draft tasks (the user has already reviewed and possibly edited these)
{_json.dumps(current_clean, indent=2)}

## User's revision feedback
{feedback}

Return JSON array where each task has the same schema as the current drafts:
- title: concise (≤80 chars)
- type: coding | bug | feature | review | investigation | repo_analysis | todo
- priority: urgent | high | normal | low
- description: 1-2 sentences. Reference real files / functions when possible.
- repo: "org/name" or ""
- category: short tag
- depends_on: array of 0-based INDICES in THIS returned array (NOT the old array — renumber if you reorder)
- plan_first: optional bool — preserve from the current draft if present

Rules:
- Preserve each current task's title / repo / category / priority / type / plan_first UNLESS the feedback explicitly asks to change it. The user may have hand-edited these.
- Add, remove, reorder, split, or merge tasks as the feedback requires.
- 3-10 tasks total unless the feedback explicitly asks for more or fewer.
- depends_on uses indices in THIS returned array; no cycles; no self-references.
- Tasks in different repos with no real dep should NOT be artificially linked.
"""

    from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
    runtime = ClaudeCodeRuntime()
    mcp_tools = runtime._discover_global_mcps()
    db.session.close()
    result = runtime.send_json(
        prompt,
        working_dir=primary_path,
        timeout=300,
        permission_mode="plan" if primary_path else None,
        allowed_tools=mcp_tools or None,
    )

    if not result.get("success") or not result.get("parsed"):
        return jsonify({"error": result.get("error", "Failed to revise tasks")}), 500

    tasks = result["parsed"]
    if not isinstance(tasks, list):
        tasks = [tasks]

    project = db.get_or_404(Project, project_id)
    _enrich_draft_tasks(tasks)

    meta = dict(project.extra or {}) if project.extra else {}
    meta["generated_tasks"] = tasks
    project.extra = meta
    db.session.commit()

    return jsonify({"tasks": tasks, "project_id": project_id})


@projects_bp.route("/projects/<project_id>/approve-plan", methods=["POST"])
def approve_plan(project_id):
    """Commit the (possibly user-edited) draft plan as real tasks and
    immediately kick off the ready ones.

    Body shape: { tasks: [...] } — same schema generate-tasks returned,
    optionally edited. Each draft may also carry a `plan_first` bool
    so the kicked-off agent starts in plan mode. If omitted, we commit
    whatever's in project.extra.generated_tasks.

    For each draft:
      - Create a Task row with a stable id (project-scoped + index).
      - Resolve depends_on indices to real task IDs.
      - Call route(task) to assign an agent (lazy-spawning if needed).
      - Set initial status: "blocked" if it has unfinished deps,
        otherwise "new".

    After commit, coding tasks in "new" status are kicked off via
    kickoff_coding_task() so the user sees agents actually running in
    Active Agents right after clicking Approve. Blocked tasks wait for
    their deps; the user can hit Launch manually if the auto-kickoff
    failed (bad repo path, no local clone, etc.).

    Project status flips to "active" on successful approval.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.orchestration import route, is_ready
    from planet_maiko.agents.coding_agent import kickoff_coding_task

    project = db.get_or_404(Project, project_id)

    data = request.get_json(silent=True) or {}
    drafts = data.get("tasks")
    if drafts is None:
        drafts = (project.extra or {}).get("generated_tasks") or []
    if not isinstance(drafts, list) or not drafts:
        return jsonify({"error": "no tasks to approve"}), 400

    # Resolve task IDs up-front so depends_on can reference them before
    # we've inserted the later ones.
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ids = [f"task-{project.id}-{now_ms}-{i:03d}" for i in range(len(drafts))]

    created = []
    for i, draft in enumerate(drafts):
        deps_idx = draft.get("depends_on") or []
        dep_ids = [ids[d] for d in deps_idx if isinstance(d, int) and 0 <= d < len(ids)]

        task = Task(
            id=ids[i],
            title=draft.get("title") or "(untitled)",
            type=draft.get("type") or "todo",
            priority=draft.get("priority") or "normal",
            project_id=project.id,
            status="new",  # fixed up below based on deps
            extra={
                "description": draft.get("description") or "",
                "repo": draft.get("repo") or "",
                "category": draft.get("category") or "",
                # Carried onto the task so /tasks/<id>/launch can
                # re-use the owner's plan-first preference if the
                # initial kickoff failed and the user retries.
                "plan_first": bool(draft.get("plan_first")),
            },
            tags=[],
            depends_on=dep_ids,
        )
        db.session.add(task)
        created.append(task)

    # Flush so route() can find them if it needs to (it doesn't, but
    # keeps the session consistent before routing).
    db.session.flush()

    # Pair drafts with their tasks by position so we can honor
    # explicit agent overrides + plan_first per task cleanly, without
    # the old lookup-by-structural-equality footgun.
    for draft, task in zip(drafts, created):
        override = draft.get("assigned_agent_id")
        if override:
            task.assigned_agent_id = override
        else:
            route(task)
        task.status = "blocked" if not is_ready(task) else "new"

    # Clear the draft now that it's committed.
    meta = dict(project.extra or {})
    meta["generated_tasks"] = None
    project.extra = meta
    # Kick the project into the active lifecycle.
    if project.status in ("planning", "approved"):
        project.status = "active"
    project.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    # Now that tasks are persisted and routed, spawn the ready coding
    # agents. Best-effort: a failure on one task surfaces in the
    # response but doesn't block the others. Blocked tasks stay
    # parked until their deps finish.
    kickoffs = []
    for draft, task in zip(drafts, created):
        if task.status != "new":
            continue
        if not task.assigned_agent_id:
            continue
        agent = db.session.get(AgentProfile, task.assigned_agent_id)
        if not agent or agent.role != "coding":
            # Review/investigation agents run via the brain cycle's
            # one-shot execute phase; no kickoff needed here.
            continue
        plan_first = bool(draft.get("plan_first"))
        result = kickoff_coding_task(task, plan_first=plan_first)
        kickoffs.append({
            "task_id": task.id,
            "title": task.title,
            "success": result.get("success", False),
            "error": result.get("error"),
            "plan_first": plan_first,
        })

    return jsonify({
        "project_id": project.id,
        "tasks_created": [t.to_dict() for t in created],
        "kickoffs": kickoffs,
    }), 201
