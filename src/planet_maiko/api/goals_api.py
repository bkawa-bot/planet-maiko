"""Standing-goal endpoints.

Surfaces the AgentGoal table to the UI. Stage 1 is deliberately thin
— the only user action is pausing/resuming or archiving a goal. Goal
creation happens via the seeder (config.github.repos) or later via
Stage 2 gap-detector proposals; no manual creation UI yet.
"""

import logging

from flask import Blueprint, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.agent_goal import AgentGoal
from planet_maiko.models.agent_profile import AgentProfile

logger = logging.getLogger(__name__)

goals_bp = Blueprint("goals", __name__)


_ALLOWED_STATUSES = {"active", "paused", "archived"}


@goals_bp.route("/goals", methods=["GET"])
def list_goals():
    """List goals. Query params:
      role=<role>        — filter by role
      scope_repo=<repo>  — filter by repo
      status=<status>    — filter by status (default: everything except archived)
    """
    q = AgentGoal.query
    role = request.args.get("role")
    if role:
        q = q.filter(AgentGoal.role == role)
    scope_repo = request.args.get("scope_repo")
    if scope_repo:
        q = q.filter(AgentGoal.scope_repo == scope_repo)
    status = request.args.get("status")
    if status:
        q = q.filter(AgentGoal.status == status)
    else:
        q = q.filter(AgentGoal.status != "archived")
    goals = q.order_by(AgentGoal.role.asc(), AgentGoal.scope_repo.asc(), AgentGoal.id.asc()).all()
    return jsonify([g.to_dict() for g in goals])


@goals_bp.route("/agents/<agent_id>/goals", methods=["GET"])
def list_goals_for_agent(agent_id):
    """Goals this agent holds — either explicitly assigned (agent_profile_id
    matches) or inherited via its role (agent_profile_id IS NULL and the
    goal's role matches the agent's role).

    Hides archived goals by default — pass ?include_archived=1 to see them.
    """
    profile = db.session.get(AgentProfile, agent_id)
    if profile is None:
        return jsonify({"error": "agent not found"}), 404

    include_archived = request.args.get("include_archived") in ("1", "true")
    q = AgentGoal.query.filter(
        db.or_(
            AgentGoal.agent_profile_id == agent_id,
            db.and_(
                AgentGoal.agent_profile_id.is_(None),
                AgentGoal.role == profile.role,
            ),
        )
    )
    if not include_archived:
        q = q.filter(AgentGoal.status != "archived")
    goals = q.order_by(AgentGoal.scope_repo.asc(), AgentGoal.id.asc()).all()
    return jsonify([g.to_dict() for g in goals])


@goals_bp.route("/goals/<int:goal_id>", methods=["PATCH"])
def update_goal(goal_id):
    """Update mutable fields on a goal. Stage 1 only supports status
    changes (active <-> paused, or archived for soft-delete).
    """
    goal = db.get_or_404(AgentGoal, goal_id)
    data = request.get_json(silent=True) or {}

    if "status" in data:
        new_status = data["status"]
        if new_status not in _ALLOWED_STATUSES:
            return jsonify({"error": f"status must be one of {sorted(_ALLOWED_STATUSES)}"}), 400
        goal.status = new_status

    db.session.commit()
    return jsonify(goal.to_dict())
