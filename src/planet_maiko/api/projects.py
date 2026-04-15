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
    from planet_maiko.orchestration import TYPE_TO_ROLE

    project = db.get_or_404(Project, project_id)

    prompt = f"""Break down this project into concrete tasks with dependencies. Return ONLY valid JSON — an array of task objects.

Project: {project.title}
Plan/Description:
{project.description or "No description provided."}

Return JSON array where each task has:
- title: concise (≤80 chars)
- type: todo | bug | feature | review | investigation | repo_analysis
- priority: urgent | high | normal | low
- description: 1-2 sentences of what to do
- repo: the repo this task belongs to (e.g. "org/auth-service"), or "" if cross-cutting
- category: short tag ("schema", "api", "ui", "test", "docs", "infra", ...)
- depends_on: array of 0-based INDICES of earlier tasks in THIS array that must complete first

Example:
[
  {{"title": "Add sessions table migration", "type": "feature", "priority": "high",
    "description": "SQL migration with index on user_id", "repo": "org/auth-service",
    "category": "schema", "depends_on": []}},
  {{"title": "Wire /sessions endpoint", "type": "feature", "priority": "normal",
    "description": "Handler + validation + tests", "repo": "org/auth-service",
    "category": "api", "depends_on": [0]}},
  {{"title": "Update web client", "type": "feature", "priority": "normal",
    "description": "Call new endpoint from login flow", "repo": "org/web-app",
    "category": "client", "depends_on": [1]}}
]

Rules:
- 3-10 tasks total
- depends_on uses indices in THIS array; no cycles; no self-references
- Tasks in different repos with no real dep should NOT be artificially linked
- If a task is purely investigation / analysis, use type "investigation"
"""

    # Release DB before long LLM call to avoid SQLite locks
    db.session.close()
    from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
    runtime = ClaudeCodeRuntime()
    # Task breakdown with dependency graph + JSON formatting is expensive
    # enough to blow past 90s on bigger plans — match generate-plan.
    result = runtime.send_json(prompt, timeout=300)

    if not result.get("success") or not result.get("parsed"):
        return jsonify({"error": result.get("error", "Failed to generate tasks")}), 500

    tasks = result["parsed"]
    if not isinstance(tasks, list):
        tasks = [tasks]

    # Enrich: pre-compute suggested agent for each draft task.
    project = db.get_or_404(Project, project_id)
    for idx, draft in enumerate(tasks):
        draft["temp_index"] = idx
        draft.setdefault("depends_on", [])
        # Sanitize depends_on — drop self-refs and out-of-range indices
        draft["depends_on"] = [
            d for d in draft["depends_on"]
            if isinstance(d, int) and 0 <= d < len(tasks) and d != idx
        ]
        role = TYPE_TO_ROLE.get(draft.get("type") or "", "coding")
        scope = draft.get("repo") or None
        draft["suggested_role"] = role
        draft["suggested_scope_repo"] = scope
        draft["suggested_agent"] = _preview_agent(role, scope)

    # Persist the draft on the project so it survives reload.
    meta = dict(project.extra or {}) if project.extra else {}
    meta["generated_tasks"] = tasks
    project.extra = meta
    db.session.commit()

    return jsonify({"tasks": tasks, "project_id": project_id})


@projects_bp.route("/projects/<project_id>/approve-plan", methods=["POST"])
def approve_plan(project_id):
    """Commit the (possibly user-edited) draft plan as real tasks.

    Body shape: { tasks: [...] } — same schema generate-tasks returned,
    optionally edited. If omitted, we commit whatever's in
    project.extra.generated_tasks.

    For each draft:
      - Create a Task row with a stable id (project-scoped + index).
      - Resolve depends_on indices to real task IDs.
      - Call route(task) to assign an agent (lazy-spawning if needed).
      - Set initial status: "blocked" if it has unfinished deps,
        otherwise "new".

    Project status flips to "active" on successful approval.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route, is_ready

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
            },
            tags=[],
            depends_on=dep_ids,
        )
        db.session.add(task)
        created.append(task)

    # Flush so route() can find them if it needs to (it doesn't, but
    # keeps the session consistent before routing).
    db.session.flush()

    # Route every task and set initial status based on dep readiness.
    for task in created:
        # Honor explicit assigned_agent_id override from the UI if present.
        draft = next((d for d in drafts if f"task-{project.id}-{now_ms}-{drafts.index(d):03d}" == task.id), None)
        override = (draft or {}).get("assigned_agent_id")
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

    return jsonify({
        "project_id": project.id,
        "tasks_created": [t.to_dict() for t in created],
    }), 201
