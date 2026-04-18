"""Home overview HTTP surface.

Thin blueprint over `planet_maiko.brain.overview`. Both routes delegate
to that module and translate Python exceptions into JSON HTTP responses.

Contract:

    GET /api/home/overview
        Return the most recent overview, regenerating on the fly if the
        cache is stale (>4h) or missing. On first-ever hit with no
        cache, this is a blocking call (~1-2 min) — the frontend shows
        a warm loading state during that wait.

    POST /api/home/overview/refresh
        Force-regenerate and return the fresh result, regardless of
        cache age.

Both responses have the same shape:

    {
        "overview": { ... parsed JSON from the LLM ... },
        "generated_at": "<iso>",
        "stale_triggered_regen": bool
    }

Failure responses are `{"error": "<message>", "last_good": {...?}}` with
a non-200 status. When there's a prior successful overview in the DB
it's attached as `last_good` so the frontend can keep showing something
while the user investigates.
"""

import json
import logging

from flask import Blueprint, jsonify

from planet_maiko.brain.overview import (
    generate_overview,
    get_latest_overview,
    _latest_skill_result,
)
from planet_maiko.database import iso_utc

logger = logging.getLogger(__name__)

home_bp = Blueprint("home", __name__)


def _last_good_payload():
    """Best-effort last-known-good overview for error response bodies.

    Returns the parsed JSON and timestamp of the most recent cached
    `home-overview` SkillResult. Returns None if nothing cached or the
    content doesn't parse.
    """
    row = _latest_skill_result()
    if row is None:
        return None
    try:
        overview = json.loads(row.content)
    except (TypeError, ValueError):
        return None
    return {
        "overview": overview,
        "generated_at": iso_utc(row.created_at),
    }


@home_bp.route("/home/overview", methods=["GET"])
def get_home_overview():
    """Return the current overview, regenerating if the cache is stale.

    Never raises; on LLM / parse failure returns a 500 with `error` and
    an optional `last_good` snapshot so the frontend can keep showing
    something.
    """
    try:
        result = get_latest_overview()
        return jsonify({
            "overview": result["overview"],
            "generated_at": result["generated_at"],
            "stale_triggered_regen": bool(result["stale"]),
        })
    except Exception as e:
        logger.exception("[home] overview generation failed: %s", e)
        body = {"error": str(e)}
        last_good = _last_good_payload()
        if last_good is not None:
            body["last_good"] = last_good
        return jsonify(body), 500


@home_bp.route("/home/overview/refresh", methods=["POST"])
def refresh_home_overview():
    """Force-regenerate the overview, bypassing the cache age check."""
    try:
        parsed = generate_overview()
        row = _latest_skill_result()
        return jsonify({
            "overview": parsed,
            "generated_at": iso_utc(row.created_at) if row else None,
            "stale_triggered_regen": True,
        })
    except Exception as e:
        logger.exception("[home] overview refresh failed: %s", e)
        body = {"error": str(e)}
        last_good = _last_good_payload()
        if last_good is not None:
            body["last_good"] = last_good
        return jsonify(body), 500
