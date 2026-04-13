import logging
import re

from flask import Blueprint, jsonify, request

from planet_maiko.themes import (
    list_themes, get_theme, save_theme, delete_theme, validate_theme,
)

logger = logging.getLogger(__name__)

themes_bp = Blueprint("themes", __name__)


def _slugify(text):
    """Convert a theme name like 'Ocean Dusk!' into 'ocean-dusk' for use as id."""
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return base[:48] or "theme"


def _unique_slug(base):
    """Suffix the slug with -2, -3, … if a theme of that id already exists."""
    if not get_theme(base):
        return base
    for i in range(2, 100):
        candidate = f"{base}-{i}"
        if not get_theme(candidate):
            return candidate
    return f"{base}-{int(__import__('time').time())}"


@themes_bp.route("/themes", methods=["GET"])
def list_themes_endpoint():
    """List all saved custom themes."""
    return jsonify(list_themes())


@themes_bp.route("/themes/<theme_id>", methods=["GET"])
def get_theme_endpoint(theme_id):
    theme = get_theme(theme_id)
    if not theme:
        return jsonify({"error": "not found"}), 404
    return jsonify(theme)


@themes_bp.route("/themes", methods=["POST"])
def create_theme_endpoint():
    """Create or overwrite a theme. Validates the payload; 400 on schema error."""
    data = request.get_json(silent=True) or {}
    theme, err = save_theme(data)
    if err:
        return jsonify({"error": err}), 400
    return jsonify(theme), 201


@themes_bp.route("/themes/<theme_id>", methods=["DELETE"])
def delete_theme_endpoint(theme_id):
    removed = delete_theme(theme_id)
    if not removed:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": theme_id})


@themes_bp.route("/themes/generate", methods=["POST"])
def generate_theme_endpoint():
    """Run the theme-designer skill with the user's vibe and return a
    validated theme. Does NOT persist — the frontend previews it and the
    user hits Save if they like it (which round-trips through
    POST /api/themes).
    """
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400

    from planet_maiko.agents.skills import get_skill_prompt
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    runtime = _get_runtime()
    if not runtime.is_available():
        return jsonify({"error": "Brain runtime not available"}), 503

    prompt = get_skill_prompt("theme-designer", {"query": query})
    if prompt is None:
        return jsonify({"error": "theme-designer skill is missing"}), 500

    model = resolve_model("skill:theme-designer")
    result = runtime.send_json(prompt, timeout=120, model=model)

    parsed = result.get("parsed") if isinstance(result, dict) else None
    if not isinstance(parsed, dict):
        # Single retry with an explicit nudge — models occasionally wrap
        # the JSON in prose on the first try.
        retry_prompt = (
            f"{prompt}\n\n"
            "Your previous output was not valid JSON. Return ONLY a JSON "
            "object matching the schema above. No markdown, no commentary."
        )
        result = runtime.send_json(retry_prompt, timeout=120, model=model)
        parsed = result.get("parsed") if isinstance(result, dict) else None
        if not isinstance(parsed, dict):
            return jsonify({"error": "theme generator did not return valid JSON"}), 502

    # The designer returns {name, emoji, colors, world_background, description}
    # but not `id`. Derive one from the name.
    theme_id = parsed.get("id") or _slugify(parsed.get("name", "theme"))
    if not data.get("overwrite"):
        theme_id = _unique_slug(theme_id)
    parsed["id"] = theme_id

    cleaned, err = validate_theme(parsed)
    if err:
        logger.warning(f"[themes] Generated theme failed validation: {err}. payload={parsed}")
        return jsonify({"error": f"Generated theme failed validation: {err}", "raw": parsed}), 502

    # Preview-only by default. If the caller passed save=true, persist it.
    if data.get("save"):
        saved, save_err = save_theme(cleaned)
        if save_err:
            return jsonify({"error": save_err, "raw": cleaned}), 400
        return jsonify({"theme": saved, "saved": True})
    return jsonify({"theme": cleaned, "saved": False})
