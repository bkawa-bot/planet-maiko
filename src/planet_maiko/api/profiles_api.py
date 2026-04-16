from flask import Blueprint, jsonify, request
from planet_maiko.database import db, iso_utc
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.agents.profiles import (
    create_profile, record_task_outcome, record_session_feedback,
    get_learning_stats, AVATARS,
)

profiles_bp = Blueprint("profiles", __name__)


@profiles_bp.route("/profiles", methods=["GET"])
def list_profiles():
    """List agent profiles with each one's 3 most-recent completed tasks.

    Query params:
        archived=true: include archived profiles in the response.
    """
    from planet_maiko.models.task import Task

    include_archived = request.args.get("archived", "false").lower() == "true"
    role = request.args.get("role")
    repo = request.args.get("repo")

    query = AgentProfile.query
    if not include_archived:
        query = query.filter((AgentProfile.archived == False) | (AgentProfile.archived == None))
    if role:
        query = query.filter(AgentProfile.role == role)
    if repo:
        # Either scoped to this repo OR global (scope_repo IS NULL) —
        # global agents cover any repo.
        query = query.filter(
            (AgentProfile.scope_repo == repo) | (AgentProfile.scope_repo.is_(None))
        )
    profiles = query.order_by(AgentProfile.tasks_completed.desc()).all()
    if not profiles:
        return jsonify([])

    # Bulk-fetch recent done tasks for every profile in one query,
    # then bucket by agent in Python. Faster than querying per profile.
    ids = [p.id for p in profiles]
    recent_tasks = (
        Task.query
        .filter(Task.assigned_agent_id.in_(ids))
        .filter(Task.status == "done")
        .order_by(Task.updated_at.desc())
        .limit(500)  # soft cap; 3 per profile from ~hundred still fits
        .all()
    )
    by_agent = {}
    for t in recent_tasks:
        bucket = by_agent.setdefault(t.assigned_agent_id, [])
        if len(bucket) < 3:
            bucket.append({
                "id": t.id,
                "title": t.title,
                "type": t.type,
                "updated_at": iso_utc(t.updated_at),
                "has_artifact": bool((t.extra or {}).get("artifact")),
            })

    out = []
    for p in profiles:
        d = p.to_dict()
        d["recent_tasks"] = by_agent.get(p.id, [])
        out.append(d)
    return jsonify(out)


@profiles_bp.route("/profiles/<profile_id>", methods=["GET"])
def get_profile(profile_id):
    """Get a single agent profile."""
    profile = db.get_or_404(AgentProfile, profile_id)
    return jsonify(profile.to_dict())


@profiles_bp.route("/profiles", methods=["POST"])
def create_agent_profile():
    """Create a new agent profile (the arrival experience).

    Body fields (all optional):
      agent_id, display_name, avatar — cosmetic identity
      role          — "coding" (default) | "review" | "investigation"
      scope_repo    — single-repo scope, e.g. "org/auth-service". Null
                      = global (e.g. a Detective for cross-repo work).
      instructions  — markdown, injected into every session this agent
                      runs. Equivalent to a per-agent AGENTS.md fragment.
    """
    data = request.get_json(silent=True) or {}
    role = data.get("role") or "coding"
    if role not in ("coding", "review", "investigation"):
        return jsonify({"error": f"invalid role: {role}"}), 400
    profile = create_profile(
        agent_id=data.get("agent_id", f"agent-{__import__('time').time_ns()}"),
        display_name=data.get("display_name"),
        avatar=data.get("avatar"),
        role=role,
        scope_repo=(data.get("scope_repo") or None),
        instructions=(data.get("instructions") or None),
    )
    return jsonify(profile.to_dict()), 201


@profiles_bp.route("/profiles/<profile_id>", methods=["PATCH"])
def update_profile(profile_id):
    """Update agent profile (rename, change avatar, role, scope, instructions)."""
    profile = db.get_or_404(AgentProfile, profile_id)
    data = request.get_json()
    if "display_name" in data:
        profile.display_name = data["display_name"]
    if "avatar" in data:
        profile.avatar = data["avatar"]
    if "flavor_text" in data:
        profile.flavor_text = data["flavor_text"]
    if "role" in data and data["role"] in ("coding", "review", "investigation"):
        profile.role = data["role"]
    if "scope_repo" in data:
        # Empty string → null (global scope).
        profile.scope_repo = data["scope_repo"] or None
    if "instructions" in data:
        profile.instructions = data["instructions"] or None
    db.session.commit()
    return jsonify(profile.to_dict())


@profiles_bp.route("/profiles/<profile_id>/archive", methods=["POST"])
def archive_profile(profile_id):
    """Archive an agent profile."""
    from datetime import datetime, timezone
    profile = db.get_or_404(AgentProfile, profile_id)
    profile.archived = True
    profile.archived_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(profile.to_dict())


@profiles_bp.route("/profiles/<profile_id>/unarchive", methods=["POST"])
def unarchive_profile(profile_id):
    """Unarchive an agent profile."""
    profile = db.get_or_404(AgentProfile, profile_id)
    profile.archived = False
    profile.archived_at = None
    db.session.commit()
    return jsonify(profile.to_dict())


@profiles_bp.route("/profiles/avatars", methods=["GET"])
def list_avatars():
    """List available avatars."""
    return jsonify(AVATARS)


@profiles_bp.route("/profiles/outcome", methods=["POST"])
def record_outcome():
    """Record task outcome for context optimization.

    Optionally accepts initial_summary and final_summary to enable
    LLM-as-judge evaluation of the task outcome quality.
    """
    data = request.get_json()
    count = record_task_outcome(
        data["task_id"],
        data["outcome"],
        initial_summary=data.get("initial_summary"),
        final_summary=data.get("final_summary"),
    )
    return jsonify({"recorded": count})


@profiles_bp.route("/profiles/feedback", methods=["POST"])
def submit_feedback():
    """Record in-session feedback to adjust agent context."""
    data = request.get_json()
    if not data or "task_id" not in data or "category" not in data:
        return jsonify({"error": "task_id and category required"}), 400

    count = record_session_feedback(
        data["task_id"],
        data["category"],
        data.get("severity", "suggestion")
    )
    return jsonify({"recorded": count, "category": data["category"]})


@profiles_bp.route("/profiles/learning-stats", methods=["GET"])
def learning_stats():
    """Get success rate stats for learnings."""
    return jsonify(get_learning_stats())
