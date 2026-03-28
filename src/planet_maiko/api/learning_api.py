from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.signal import Signal
from planet_maiko.models.learning import Learning

learning_bp = Blueprint("learning", __name__)


# --- Signals ---

@learning_bp.route("/signals", methods=["GET"])
def list_signals():
    """List signals, optionally filtered."""
    category = request.args.get("category")
    source_type = request.args.get("source_type")
    aggregated = request.args.get("aggregated")

    query = Signal.query
    if category:
        query = query.filter_by(category=category)
    if source_type:
        query = query.filter_by(source_type=source_type)
    if aggregated is not None:
        query = query.filter_by(aggregated=aggregated.lower() == "true")

    signals = query.order_by(Signal.created_at.desc()).limit(100).all()
    return jsonify([s.to_dict() for s in signals])


@learning_bp.route("/signals", methods=["POST"])
def create_signal():
    """Record a new feedback signal."""
    data = request.get_json()
    signal = Signal(
        category=data["category"],
        text=data["text"],
        source_type=data.get("source_type", "manual"),
        reviewer=data.get("reviewer"),
        severity=data.get("severity", "suggestion"),
        repo=data.get("repo"),
        language=data.get("language"),
        file_path=data.get("file_path"),
    )
    db.session.add(signal)
    db.session.commit()
    return jsonify(signal.to_dict()), 201


# --- Learnings ---

@learning_bp.route("/learnings", methods=["GET"])
def list_learnings():
    """List learnings, optionally filtered by status or category."""
    status = request.args.get("status")
    category = request.args.get("category")

    query = Learning.query
    if status:
        query = query.filter_by(status=status)
    if category:
        query = query.filter_by(category=category)

    learnings = query.order_by(Learning.confidence.desc()).all()
    return jsonify([l.to_dict() for l in learnings])


@learning_bp.route("/learnings/<int:learning_id>", methods=["GET"])
def get_learning(learning_id):
    """Get a learning with its signals."""
    learning = db.get_or_404(Learning, learning_id)
    data = learning.to_dict()
    data["signals"] = [s.to_dict() for s in learning.signals]
    return jsonify(data)


@learning_bp.route("/learnings", methods=["POST"])
def create_learning():
    """Manually create a learning (skips signal aggregation)."""
    data = request.get_json()
    learning = Learning(
        rule=data["rule"],
        category=data["category"],
        scope_repo=data.get("scope_repo"),
        scope_language=data.get("scope_language"),
        confidence=1.0,
        source="manual",
        status="active",
    )
    db.session.add(learning)
    db.session.commit()
    return jsonify(learning.to_dict()), 201


@learning_bp.route("/learnings/<int:learning_id>/approve", methods=["POST"])
def approve_learning(learning_id):
    """Approve a pending learning → active."""
    learning = db.get_or_404(Learning, learning_id)
    learning.status = "active"
    db.session.commit()
    return jsonify(learning.to_dict())


@learning_bp.route("/learnings/<int:learning_id>/dismiss", methods=["POST"])
def dismiss_learning(learning_id):
    """Dismiss a learning."""
    learning = db.get_or_404(Learning, learning_id)
    learning.status = "dismissed"
    db.session.commit()
    return jsonify(learning.to_dict())


@learning_bp.route("/learnings/<int:learning_id>", methods=["PATCH"])
def edit_learning(learning_id):
    """Edit a learning's rule text or category."""
    learning = db.get_or_404(Learning, learning_id)
    data = request.get_json()
    if "rule" in data:
        learning.rule = data["rule"]
    if "category" in data:
        learning.category = data["category"]
    if "scope_repo" in data:
        learning.scope_repo = data["scope_repo"]
    if "scope_language" in data:
        learning.scope_language = data["scope_language"]
    db.session.commit()
    return jsonify(learning.to_dict())


@learning_bp.route("/learnings/brief", methods=["GET"])
def learning_brief():
    """Compile active learnings into a brief for agents.

    Optional query params: repo, language (to scope the brief)
    """
    from planet_maiko.brain.learning.processor import compile_brief
    repo = request.args.get("repo")
    language = request.args.get("language")
    brief = compile_brief(repo=repo, language=language)
    return jsonify({"brief": brief})
