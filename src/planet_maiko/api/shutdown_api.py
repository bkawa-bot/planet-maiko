"""Shutdown / cleanup API.

Two endpoints:
    GET  /api/shutdown/preview         — counts per cleanup step
    POST /api/shutdown/step            — { name } → result for that step

Per-step endpoints (vs. a single run-all) so the frontend can narrate
each one as it happens and show incremental progress.
"""

from flask import Blueprint, jsonify, request

from planet_maiko.shutdown import preview, STEPS

shutdown_bp = Blueprint("shutdown", __name__)


@shutdown_bp.route("/shutdown/preview", methods=["GET"])
def shutdown_preview():
    return jsonify(preview())


@shutdown_bp.route("/shutdown/step", methods=["POST"])
def shutdown_step():
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    fn = STEPS.get(name)
    if fn is None:
        return jsonify({"error": f"unknown step: {name}"}), 400
    try:
        result = fn()
    except Exception as e:
        return jsonify({"error": str(e), "step": name}), 500
    return jsonify({"step": name, **result})
