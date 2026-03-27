from flask import Blueprint, jsonify, request
from planet_maiko.brain.cycle import run, get_status
from planet_maiko.brain.pupdates.rules import load_rules
from planet_maiko.brain.tasks.scheduler import compute_schedule
from planet_maiko.brain.guardrails import get_permission_level

brain_bp = Blueprint("brain", __name__)


@brain_bp.route("/brain/status", methods=["GET"])
def brain_status():
    """Get brain cycle status."""
    return jsonify(get_status())


@brain_bp.route("/brain/rules", methods=["GET"])
def brain_rules():
    """Get the current rules."""
    rules = load_rules()
    return jsonify(rules)


@brain_bp.route("/brain/cycle", methods=["POST"])
def trigger_cycle():
    """Manually trigger a brain cycle."""
    from flask import current_app
    results = run(current_app._get_current_object())
    return jsonify(results)


@brain_bp.route("/brain/schedule", methods=["GET"])
def get_schedule():
    """Get the optimized task schedule."""
    return jsonify(compute_schedule())


@brain_bp.route("/brain/guardrails/<action>", methods=["GET"])
def check_guardrail(action):
    """Check permission level for an action."""
    return jsonify({
        "action": action,
        "level": get_permission_level(action),
    })
