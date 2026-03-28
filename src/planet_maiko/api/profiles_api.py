from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.agents.profiles import (
    create_profile, record_task_outcome, recommend_agent,
    get_learning_stats, AVATARS,
)

profiles_bp = Blueprint("profiles", __name__)


@profiles_bp.route("/profiles", methods=["GET"])
def list_profiles():
    """List all agent profiles."""
    profiles = AgentProfile.query.order_by(AgentProfile.tasks_completed.desc()).all()
    return jsonify([p.to_dict() for p in profiles])


@profiles_bp.route("/profiles/<profile_id>", methods=["GET"])
def get_profile(profile_id):
    """Get a single agent profile."""
    profile = db.get_or_404(AgentProfile, profile_id)
    return jsonify(profile.to_dict())


@profiles_bp.route("/profiles", methods=["POST"])
def create_agent_profile():
    """Create a new agent profile (the arrival experience)."""
    data = request.get_json(silent=True) or {}
    profile = create_profile(
        agent_id=data.get("agent_id", f"agent-{__import__('time').time_ns()}"),
        display_name=data.get("display_name"),
        avatar=data.get("avatar"),
    )
    return jsonify(profile.to_dict()), 201


@profiles_bp.route("/profiles/<profile_id>", methods=["PATCH"])
def update_profile(profile_id):
    """Update agent profile (rename, change avatar, etc.)."""
    profile = db.get_or_404(AgentProfile, profile_id)
    data = request.get_json()
    if "display_name" in data:
        profile.display_name = data["display_name"]
    if "avatar" in data:
        profile.avatar = data["avatar"]
    if "flavor_text" in data:
        profile.flavor_text = data["flavor_text"]
    db.session.commit()
    return jsonify(profile.to_dict())


@profiles_bp.route("/profiles/avatars", methods=["GET"])
def list_avatars():
    """List available avatars."""
    return jsonify(AVATARS)


@profiles_bp.route("/profiles/recommend", methods=["GET"])
def recommend():
    """Recommend best agent for a task."""
    repo = request.args.get("repo")
    task_type = request.args.get("task_type")
    return jsonify(recommend_agent(repo=repo, task_type=task_type))


@profiles_bp.route("/profiles/outcome", methods=["POST"])
def record_outcome():
    """Record task outcome for context optimization."""
    data = request.get_json()
    count = record_task_outcome(data["task_id"], data["outcome"])
    return jsonify({"recorded": count})


@profiles_bp.route("/profiles/learning-stats", methods=["GET"])
def learning_stats():
    """Get success rate stats for learnings."""
    return jsonify(get_learning_stats())
