"""HTTP surface for the Ask the Pack dispatcher.

Single endpoint:

    POST /api/pack/dispatch
        Body: {"request": str, "context"?: str}
        Response: whatever planet_maiko.pack.dispatch returns, JSON-encoded.

The dispatcher handles the actual routing + launching; this module
just parses the request body and translates Python exceptions into
HTTP responses.
"""

import logging

from flask import Blueprint, jsonify, request

from planet_maiko.pack import dispatch

logger = logging.getLogger(__name__)

pack_bp = Blueprint("pack", __name__)


@pack_bp.route("/pack/dispatch", methods=["POST"])
def dispatch_route():
    data = request.get_json(silent=True) or {}
    user_request = (data.get("request") or "").strip()
    context = (data.get("context") or "").strip()

    if not user_request:
        return jsonify({"status": "error", "error": "request is required"}), 400

    try:
        result = dispatch(user_request, context=context)
    except Exception as e:
        logger.exception("[pack] dispatch crashed: %s", e)
        return jsonify({"status": "error", "error": str(e)}), 500

    status = result.get("status")
    if status == "error":
        return jsonify(result), 500
    return jsonify(result), 200
