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
    return data


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
