from flask import Blueprint, jsonify, request
from planet_maiko.brain.creativity.scene import generate
from planet_maiko.brain.suggestions.scanner import quick_scan
from planet_maiko.brain.awareness.expertise import get_graph, get_experts_for, build as build_expertise, should_rebuild
from planet_maiko.brain.awareness.conflicts import detect_conflicts, send_conflict_warnings

scene_bp = Blueprint("scene", __name__)


# --- Scene ---

@scene_bp.route("/scene", methods=["GET"])
def get_scene():
    """Get current pixel art scene descriptor."""
    weather = request.args.get("weather", "clear")
    temp = int(request.args.get("temperature_f", "70"))
    scene = generate(weather=weather, temperature_f=temp)
    return jsonify(scene)


# --- Suggestions ---

@scene_bp.route("/suggestions/scan", methods=["POST"])
def run_scan():
    """Run quick suggestion scan."""
    data = request.get_json(silent=True) or {}
    repos = data.get("repos", [])
    result = quick_scan(repos=repos)
    return jsonify(result)


# --- Expertise ---

@scene_bp.route("/expertise", methods=["GET"])
def expertise_graph():
    """Get the expertise graph."""
    return jsonify(get_graph())


@scene_bp.route("/expertise/experts", methods=["GET"])
def find_experts():
    """Find experts for a repo/path."""
    repo = request.args.get("repo", "")
    path_prefix = request.args.get("path")
    experts = get_experts_for(repo, path_prefix)
    return jsonify(experts)


@scene_bp.route("/expertise/build", methods=["POST"])
def rebuild_expertise():
    """Rebuild the expertise graph."""
    data = request.get_json(silent=True) or {}
    repos = data.get("repos", [])
    result = build_expertise(repos)
    return jsonify(result)


# --- Awareness ---

@scene_bp.route("/awareness/check", methods=["POST"])
def check_conflicts():
    """Check for conflicts between active agents."""
    data = request.get_json(silent=True) or {}
    worktrees = data.get("agent_worktrees", [])
    conflicts = detect_conflicts(worktrees)
    if conflicts:
        warnings = send_conflict_warnings(conflicts)
        return jsonify({"conflicts": conflicts, "warnings_sent": warnings})
    return jsonify({"conflicts": [], "warnings_sent": 0})
