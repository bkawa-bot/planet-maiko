from flask import Blueprint, jsonify, request
from planet_maiko.brain.learning.pack_insights import (
    get_state, start_gathering, collect_from_agents,
    add_manual_learning, synthesize, finalize, reset,
)

pack_insights_bp = Blueprint("pack_insights", __name__)


@pack_insights_bp.route("/pack-insights", methods=["GET"])
def pack_insights_state():
    """Get current Pack Insights gathering state."""
    return jsonify(get_state())


@pack_insights_bp.route("/pack-insights/start", methods=["POST"])
def pack_insights_start():
    """Start Pack Insights gathering."""
    return jsonify(start_gathering())


@pack_insights_bp.route("/pack-insights/collect", methods=["POST"])
def pack_insights_collect():
    """Collect learnings from agents."""
    return jsonify(collect_from_agents())


@pack_insights_bp.route("/pack-insights/add", methods=["POST"])
def pack_insights_add():
    """Add a manual learning during review."""
    data = request.get_json()
    result = add_manual_learning(data["text"], data.get("category", "domain_knowledge"))
    return jsonify(result)


@pack_insights_bp.route("/pack-insights/synthesize", methods=["POST"])
def pack_insights_synthesize():
    """Run synthesis (dedupe, conflict detection, propose rules)."""
    return jsonify(synthesize())


@pack_insights_bp.route("/pack-insights/finalize", methods=["POST"])
def pack_insights_finalize():
    """Finalize and merge learnings into the global pool."""
    data = request.get_json(silent=True) or {}
    decisions = data.get("decisions", {})
    return jsonify(finalize(decisions))


@pack_insights_bp.route("/pack-insights/reset", methods=["POST"])
def pack_insights_reset():
    """Reset Pack Insights state back to idle."""
    reset()
    return jsonify({"status": "idle"})
