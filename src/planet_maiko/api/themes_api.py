from flask import Blueprint, jsonify, request

from planet_maiko.themes import (
    list_themes, get_theme, save_theme, delete_theme,
)

themes_bp = Blueprint("themes", __name__)


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
