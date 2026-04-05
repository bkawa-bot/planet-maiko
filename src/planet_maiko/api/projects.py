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


@projects_bp.route("/projects/<project_id>/generate-plan", methods=["POST"])
def generate_plan(project_id):
    """Use LLM to generate a multi-phase plan for a project."""
    project = db.get_or_404(Project, project_id)

    try:
        from planet_maiko.agents.brain_session import BrainSession
        session = BrainSession()
        if not session.runtime or not session.runtime.is_available():
            return jsonify({"error": "Runtime not available"}), 503

        prompt = (
            f"Break this project into 2-5 implementation phases.\n\n"
            f"Project: {project.title}\n"
            f"Description: {project.description or 'No description'}\n\n"
            f"For each phase, provide:\n"
            f"- title: short name\n"
            f"- description: what to implement\n"
            f"- repo: which repository (if known)\n\n"
            f"Respond in JSON: {{\"phases\": [{{\"title\": \"...\", \"description\": \"...\", \"repo\": \"...\"}}]}}"
        )

        result = session.runtime.send_json(prompt, timeout=30)
        if result and "phases" in result:
            phases = []
            for i, p in enumerate(result["phases"]):
                phases.append({
                    "number": i,
                    "title": p.get("title", f"Phase {i+1}"),
                    "description": p.get("description", ""),
                    "repo": p.get("repo", ""),
                    "status": "active" if i == 0 else "pending",
                    "depends_on": [i-1] if i > 0 else [],
                })
            project.phases = phases
            project.current_phase = 0
            db.session.commit()
            return jsonify({"phases": phases})

        return jsonify({"error": "Could not generate plan"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@projects_bp.route("/projects/<project_id>/generate-tasks", methods=["POST"])
def generate_tasks(project_id):
    """Use LLM to generate task breakdown from project description."""
    project = db.get_or_404(Project, project_id)

    from planet_maiko.agents.brain_session import run_skill
    from planet_maiko.agents.skills import get_skill_prompt

    prompt = f"""Break down this project into concrete tasks. Return ONLY valid JSON — an array of task objects.

Project: {project.title}
Description: {project.description or "No description provided."}

Return JSON array like:
[
  {{"title": "Task title", "type": "todo", "priority": "normal", "description": "What to do"}},
  ...
]

Rules:
- 3-8 tasks maximum
- Each task should be a single clear action
- Set priority: urgent, high, normal, or low
- Set type: todo, bug, feature, or review
- Keep titles concise (under 80 chars)
- Order by suggested execution sequence"""

    from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
    runtime = ClaudeCodeRuntime()
    result = runtime.send_json(prompt, timeout=60)

    if not result.get("success") or not result.get("parsed"):
        return jsonify({"error": result.get("error", "Failed to generate tasks")}), 500

    tasks = result["parsed"]
    if not isinstance(tasks, list):
        tasks = [tasks]

    return jsonify({"tasks": tasks, "project_id": project_id})
