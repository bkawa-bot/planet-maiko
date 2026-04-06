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


@learning_bp.route("/learnings/backfill", methods=["POST"])
def backfill_learnings():
    """Scan past PRs for review comments and create learnings.

    Runs the full pipeline: bootstrap → classify → aggregate.
    """
    from planet_maiko.brain.learning.bootstrap import bootstrap_from_prs
    from planet_maiko.brain.learning.classifier import classify_unclassified_signals
    from planet_maiko.brain.learning.processor import process_signals

    data = request.get_json(silent=True) or {}
    limit = data.get("limit", 20)

    signals_created = bootstrap_from_prs(limit=limit)

    classified = 0
    classify_error = None
    try:
        classified = classify_unclassified_signals(batch_size=50)
    except Exception as e:
        classify_error = str(e)

    learning_results = process_signals()

    result = {
        "signals_created": signals_created,
        "classified": classified,
        "new_learnings": learning_results.get("new_learnings", 0),
        "graduated": learning_results.get("graduated", 0),
    }
    if classify_error:
        result["classify_note"] = f"LLM classification unavailable: {classify_error}. Signals saved but not categorized yet."
    return jsonify(result)


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


# === Tournaments ===

@learning_bp.route("/tournaments", methods=["GET"])
def list_tournaments():
    """List tournament history."""
    from planet_maiko.models.tournament import Tournament
    tournaments = Tournament.query.order_by(Tournament.created_at.desc()).limit(20).all()
    return jsonify([t.to_dict() for t in tournaments])


@learning_bp.route("/tournaments/<int:tournament_id>", methods=["GET"])
def get_tournament(tournament_id):
    """Get a tournament with its entries."""
    from planet_maiko.models.tournament import Tournament
    t = db.get_or_404(Tournament, tournament_id)
    return jsonify(t.to_dict())


@learning_bp.route("/tournaments/run", methods=["POST"])
def run_tournament_endpoint():
    """Manually trigger a tournament on a specific PR."""
    from flask import current_app
    from planet_maiko.brain.learning.tournament import run_tournament
    data = request.get_json()
    repo = data.get("repo")
    pr_number = data.get("pr_number")
    if not repo or not pr_number:
        return jsonify({"error": "repo and pr_number required"}), 400

    result = run_tournament(repo, int(pr_number), current_app._get_current_object())
    if result:
        return jsonify(result)
    return jsonify({"error": "Tournament failed"}), 500


@learning_bp.route("/tournaments/scores", methods=["GET"])
def tournament_scores():
    """Get rule leaderboard from tournament data."""
    from planet_maiko.brain.learning.tournament import get_tournament_scores
    repo = request.args.get("repo")
    scores = get_tournament_scores(repo=repo)

    # Enrich with rule text
    leaderboard = []
    for lid, info in sorted(scores.items(), key=lambda x: -x[1]["avg_score"]):
        learning = db.session.get(Learning, lid)
        if learning:
            leaderboard.append({
                "learning_id": lid,
                "rule": learning.rule,
                "category": learning.category,
                "avg_score": round(info["avg_score"], 3),
                "tournament_count": info["tournament_count"],
            })

    return jsonify(leaderboard)


@learning_bp.route("/tournaments/suggested-tags", methods=["GET"])
def suggested_tags():
    """Get tags the LLM suggested that aren't in the approved list."""
    from planet_maiko.brain.learning.tournament import get_suggested_tags
    return jsonify(get_suggested_tags())
