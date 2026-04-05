import logging

from flask import Blueprint, jsonify, request
from planet_maiko.brain.awareness.conflicts import detect_conflicts, send_conflict_warnings

logger = logging.getLogger(__name__)

awareness_bp = Blueprint("awareness", __name__)


@awareness_bp.route("/awareness/check", methods=["POST"])
def check_conflicts():
    """Check for conflicts between active agents."""
    data = request.get_json(silent=True) or {}
    worktrees = data.get("agent_worktrees", [])
    conflicts = detect_conflicts(worktrees)
    if conflicts:
        warnings = send_conflict_warnings(conflicts)
        return jsonify({"conflicts": conflicts, "warnings_sent": warnings})
    return jsonify({"conflicts": [], "warnings_sent": 0})
