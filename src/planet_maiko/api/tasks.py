from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.task import Task

tasks_bp = Blueprint("tasks", __name__)


@tasks_bp.route("/tasks", methods=["GET"])
def list_tasks():
    """List tasks with optional filtering by status, priority, or project."""
    status = request.args.get("status")
    priority = request.args.get("priority")
    project_id = request.args.get("project_id")

    query = Task.query
    if status:
        query = query.filter_by(status=status)
    if priority:
        query = query.filter_by(priority=priority)
    if project_id:
        query = query.filter_by(project_id=project_id)

    tasks = query.order_by(Task.created_at.desc()).all()
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

    for field in ["title", "type", "priority", "url", "tags", "project_id", "assigned_agent_id", "due_date"]:
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
    """Mark a task as done."""
    task = db.get_or_404(Task, task_id)
    if task.status == "cancelled":
        return jsonify({"error": "Cannot complete a cancelled task"}), 400
    task.status = "done"
    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(task.to_dict())


@tasks_bp.route("/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task(task_id):
    """Cancel a task."""
    task = db.get_or_404(Task, task_id)
    task.status = "cancelled"
    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(task.to_dict())


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
