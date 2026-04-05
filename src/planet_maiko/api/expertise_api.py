import logging

from flask import Blueprint, jsonify, request
from planet_maiko.brain.awareness.expertise import (
    get_graph,
    get_experts_for,
    build as build_expertise,
)

logger = logging.getLogger(__name__)

expertise_bp = Blueprint("expertise", __name__)


@expertise_bp.route("/expertise", methods=["GET"])
def expertise_graph():
    """Get the expertise graph."""
    return jsonify(get_graph())


@expertise_bp.route("/expertise/experts", methods=["GET"])
def find_experts():
    """Find experts for a repo/path."""
    repo = request.args.get("repo", "")
    path_prefix = request.args.get("path")
    experts = get_experts_for(repo, path_prefix)
    return jsonify(experts)


@expertise_bp.route("/expertise/reviewers", methods=["GET"])
def get_reviewer_profiles():
    """Get reviewer focus profiles."""
    from planet_maiko.brain.awareness.expertise import build_reviewer_profiles
    profiles = build_reviewer_profiles()
    return jsonify(profiles)


@expertise_bp.route("/expertise/build", methods=["POST"])
def rebuild_expertise():
    """Rebuild the expertise graph."""
    data = request.get_json(silent=True) or {}
    repos = data.get("repos", [])
    result = build_expertise(repos)
    return jsonify(result)
