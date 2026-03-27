from flask import Blueprint, jsonify, request
from planet_maiko.brain.learning.eod import (
    get_state, start_gathering, collect_from_agents,
    add_manual_learning, synthesize, finalize, reset,
)

eod_bp = Blueprint("eod", __name__)


@eod_bp.route("/eod", methods=["GET"])
def eod_state():
    """Get current EOD gathering state."""
    return jsonify(get_state())


@eod_bp.route("/eod/start", methods=["POST"])
def eod_start():
    """Start EOD gathering."""
    return jsonify(start_gathering())


@eod_bp.route("/eod/collect", methods=["POST"])
def eod_collect():
    """Collect learnings from agents."""
    return jsonify(collect_from_agents())


@eod_bp.route("/eod/add", methods=["POST"])
def eod_add():
    """Add a manual learning during review."""
    data = request.get_json()
    result = add_manual_learning(data["text"], data.get("category", "domain_knowledge"))
    return jsonify(result)


@eod_bp.route("/eod/synthesize", methods=["POST"])
def eod_synthesize():
    """Run synthesis (dedupe, conflict detection, propose rules)."""
    return jsonify(synthesize())


@eod_bp.route("/eod/finalize", methods=["POST"])
def eod_finalize():
    """Finalize and merge learnings into the global pool."""
    data = request.get_json(silent=True) or {}
    decisions = data.get("decisions", {})
    return jsonify(finalize(decisions))


@eod_bp.route("/eod/reset", methods=["POST"])
def eod_reset():
    """Reset EOD state back to idle."""
    reset()
    return jsonify({"status": "idle"})
