from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.task import Task

tasks_bp = Blueprint("tasks", __name__)


@tasks_bp.route("/tasks", methods=["GET"])
def list_tasks():
    """List tasks with optional filtering and pagination."""
    status = request.args.get("status")
    priority = request.args.get("priority")
    project_id = request.args.get("project_id")
    limit = min(int(request.args.get("limit", 200)), 500)
    offset = int(request.args.get("offset", 0))

    query = Task.query
    if status:
        query = query.filter_by(status=status)
    if priority:
        query = query.filter_by(priority=priority)
    if project_id:
        query = query.filter_by(project_id=project_id)

    tasks = query.order_by(Task.created_at.asc()).limit(limit).offset(offset).all()
    return jsonify([t.to_dict() for t in tasks])


@tasks_bp.route("/tasks/<task_id>", methods=["GET"])
def get_task(task_id):
    """Get a single task by ID."""
    task = db.get_or_404(Task, task_id)
    return jsonify(task.to_dict())


@tasks_bp.route("/tasks", methods=["POST"])
def create_task():
    """Create a new task."""
    data = request.get_json()
    task = Task(
        id=data["id"],
        title=data["title"],
        type=data.get("type", "todo"),
        status=data.get("status", "new"),
        priority=data.get("priority", "normal"),
        source_pupdate_id=data.get("source_pupdate_id"),
        project_id=data.get("project_id"),
        url=data.get("url"),
        tags=data.get("tags", []),
        extra=data.get("metadata", {}),
        due_date=data.get("due_date"),
    )
    db.session.add(task)
    db.session.commit()

    from planet_maiko.plugins.loader import fire_hook
    fire_hook("on_task_created", task)

    return jsonify(task.to_dict()), 201


@tasks_bp.route("/tasks/<task_id>", methods=["PATCH"])
def update_task(task_id):
    """Update task fields (title, priority, tags, metadata)."""
    task = db.get_or_404(Task, task_id)
    data = request.get_json()

    for field in ["title", "type", "status", "priority", "url", "tags", "project_id", "assigned_agent_id", "due_date"]:
        if field in data:
            setattr(task, field, data[field])
    if "metadata" in data:
        task.extra = data["metadata"]

    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(task.to_dict())


@tasks_bp.route("/tasks/<task_id>/start", methods=["POST"])
def start_task(task_id):
    """Mark a task as in progress."""
    task = db.get_or_404(Task, task_id)
    if task.status in ("done", "cancelled"):
        return jsonify({"error": f"Cannot start a {task.status} task"}), 400
    task.status = "in_progress"
    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(task.to_dict())


@tasks_bp.route("/tasks/<task_id>/done", methods=["POST"])
def complete_task(task_id):
    """Mark a task as done — deletes it from the active list."""
    task = db.get_or_404(Task, task_id)
    db.session.delete(task)
    db.session.commit()
    return jsonify({"status": "deleted", "id": task_id})


@tasks_bp.route("/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task(task_id):
    """Cancel a task — deletes it from the active list."""
    task = db.get_or_404(Task, task_id)
    db.session.delete(task)
    db.session.commit()
    return jsonify({"status": "deleted", "id": task_id})


@tasks_bp.route("/tasks/<task_id>/reassign", methods=["POST"])
def reassign_task(task_id):
    """Reassign a task to a different agent.

    Body: { agent_id?: str }. If agent_id is omitted, the router picks a
    different agent of the same role for the task's scope, lazy-spawning
    if needed. If the same agent would be picked again, returns 409.
    """
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.orchestration import role_for_task, scope_for_task, find_profile, maybe_spawn

    task = db.get_or_404(Task, task_id)
    data = request.get_json(silent=True) or {}
    current = task.assigned_agent_id
    target_id = data.get("agent_id")

    if target_id:
        profile = db.session.get(AgentProfile, target_id)
        if not profile:
            return jsonify({"error": "agent not found"}), 404
        task.assigned_agent_id = profile.id
    else:
        # Auto-pick: force a new agent of the same role. Archive the
        # current so the router's simple find-match lookup skips it,
        # spawn a replacement.
        if current:
            old = db.session.get(AgentProfile, current)
            if old:
                old.archived = True
                old.archived_at = datetime.now(timezone.utc)
        role = role_for_task(task)
        scope = scope_for_task(task)
        found = find_profile(role, scope)
        new_profile = found or maybe_spawn(role, scope)
        if new_profile.id == current:
            return jsonify({"error": "no alternative agent available"}), 409
        task.assigned_agent_id = new_profile.id

    # Reset status so the new agent picks it up next cycle.
    if task.status == "in_progress":
        task.status = "new"
    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(task.to_dict())


@tasks_bp.route("/tasks/<task_id>/linear", methods=["POST"])
def send_task_to_linear(task_id):
    """Create a Linear issue from a Maiko task.

    Stores the new Linear id/identifier/url on task.extra so the UI can
    deep-link and avoid creating duplicates.
    """
    from planet_maiko.pollers.linear_poller import LinearPoller

    task = db.get_or_404(Task, task_id)
    extra = dict(task.extra or {})

    # If this task is already linked to a Linear issue (either sent before
    # or imported from Linear originally), hand back what we have.
    if extra.get("linear_identifier") or extra.get("identifier"):
        return jsonify({
            "already_synced": True,
            "linear_id": extra.get("linear_id"),
            "linear_identifier": extra.get("linear_identifier") or extra.get("identifier"),
            "linear_url": extra.get("linear_url") or task.url,
        })

    data = request.get_json(silent=True) or {}

    # Description falls back to the originating pupdate's body.
    description = data.get("description")
    if description is None:
        description = extra.get("description") or ""
        if not description and task.source_pupdate is not None:
            description = task.source_pupdate.body or ""

    try:
        issue = LinearPoller.create_issue(
            task,
            description=description,
            team_id=data.get("team_id") or None,
            project_id=data.get("project_id") or None,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Linear create failed: {e}"}), 502

    extra["linear_id"] = issue["id"]
    extra["linear_identifier"] = issue["identifier"]
    extra["linear_url"] = issue["url"]
    task.extra = extra
    db.session.commit()
    return jsonify({
        "success": True,
        "linear_id": issue["id"],
        "linear_identifier": issue["identifier"],
        "linear_url": issue["url"],
    }), 201


@tasks_bp.route("/tasks/import-linear", methods=["POST"])
def import_from_linear():
    """Import assigned issues from Linear as tasks with project associations."""
    from planet_maiko.config import load_config
    config = load_config()
    api_key = config.get("linear", {}).get("api_key")
    if not api_key:
        return jsonify({"error": "Linear API key not configured. Set it in Settings."}), 400

    from planet_maiko.pollers.linear_poller import LinearPoller
    stats = LinearPoller.import_issues(api_key)
    return jsonify(stats)
