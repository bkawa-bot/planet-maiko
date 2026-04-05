from flask import Blueprint, jsonify, request
from planet_maiko.brain.focus.manager import get_state, set_state, get_digest, get_held

focus_bp = Blueprint("focus", __name__)


@focus_bp.route("/focus", methods=["GET"])
def focus_state():
    """Get current focus state."""
    return jsonify(get_state())


@focus_bp.route("/focus", methods=["POST"])
def update_focus():
    """Set focus state."""
    data = request.get_json()
    result = set_state(
        new_state=data["state"],
        duration_minutes=data.get("duration_minutes"),
        trigger=data.get("trigger", "explicit"),
    )
    return jsonify(result)


@focus_bp.route("/focus/digest", methods=["GET"])
def focus_digest():
    """Get digest of held pupdates."""
    return jsonify(get_digest())


@focus_bp.route("/focus/held", methods=["GET"])
def focus_held():
    """Get all held pupdates."""
    held = get_held()
    return jsonify([p.to_dict() for p in held])
