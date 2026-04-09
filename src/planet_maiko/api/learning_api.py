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
        code_context=data.get("code_context"),
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


@learning_bp.route("/learnings/classify", methods=["POST"])
def classify_pending():
    """Manually classify pending pattern signals via LLM (clean up backlog)."""
    from planet_maiko.brain.learning.classifier import classify_unclassified_signals
    from planet_maiko.brain.learning.processor import process_signals

    data = request.get_json(silent=True) or {}
    batch_size = data.get("batch_size", 50)

    # Release DB before LLM call
    db.session.close()

    classified = classify_unclassified_signals(batch_size=batch_size)
    learning_results = process_signals()

    return jsonify({
        "classified": classified,
        "new_learnings": learning_results.get("new_learnings", 0),
        "graduated": learning_results.get("graduated", 0),
    })


@learning_bp.route("/learnings/backfill", methods=["POST"])
def backfill_learnings():
    """Scan past PRs, synthesize comments into clean learnings via LLM."""
    from planet_maiko.brain.learning.bootstrap import bootstrap_from_prs
    from planet_maiko.brain.learning.processor import process_signals

    data = request.get_json(silent=True) or {}
    limit = data.get("limit", 20)
    repo = data.get("repo")  # optional: scan only this one repo

    # Step 1: Pull raw PR comments as signals
    repos = [repo] if repo else None
    bootstrap_result = bootstrap_from_prs(limit=limit, repos=repos)
    signals_created = bootstrap_result["total_created"]
    per_repo = bootstrap_result["per_repo"]

    if signals_created == 0:
        return jsonify({
            "signals_created": 0, "synthesized": 0,
            "new_learnings": 0, "graduated": 0,
            "per_repo": per_repo,
        })

    # Step 2: LLM synthesis — transform raw comments into clean learnings
    synthesized = 0
    synth_error = None
    try:
        from planet_maiko.models.signal import Signal as BackfillSignal
        from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
        from planet_maiko.agents.routing import resolve_model

        raw = BackfillSignal.query.filter_by(
            source_type="pr_comment", aggregated=False
        ).all()

        if raw:
            # Build batch prompt
            comments = []
            for i, s in enumerate(raw[:20]):
                comments.append(f"{i+1}. [{s.repo or 'unknown'}] {s.text[:300]}")

            prompt = f"""Synthesize these PR review comments into clean, actionable coding rules.

For each comment, extract the core lesson as a short rule (one sentence).
Also classify each into a category.

Comments:
{chr(10).join(comments)}

Categories: security, error_handling, testing, performance, api_design,
architecture, null_safety, style, naming, docs, pattern, domain_knowledge

Respond as JSON: {{"rules": [{{"index": 1, "rule": "Always validate input lengths at API boundaries", "category": "security"}}, ...]}}"""

            # Release DB before long LLM call to avoid SQLite locks
            signal_ids = [s.id for s in raw]
            db.session.close()

            runtime = ClaudeCodeRuntime()
            result = runtime.send_json(prompt, timeout=90, model=resolve_model("classify"))

            if result.get("parsed") and "rules" in result["parsed"]:
                from planet_maiko.models.signal import Signal as RefetchSignal
                refetched = RefetchSignal.query.filter(RefetchSignal.id.in_(signal_ids)).all()
                for rule_data in result["parsed"]["rules"]:
                    idx = rule_data.get("index", 0) - 1
                    if 0 <= idx < len(refetched):
                        refetched[idx].text = rule_data.get("rule", refetched[idx].text)
                        refetched[idx].category = rule_data.get("category", "pattern")
                        synthesized += 1

                db.session.commit()
    except Exception as e:
        synth_error = str(e)

    # Step 3: Aggregate into learnings
    learning_results = process_signals()

    result = {
        "signals_created": signals_created,
        "synthesized": synthesized,
        "new_learnings": learning_results.get("new_learnings", 0),
        "graduated": learning_results.get("graduated", 0),
        "per_repo": per_repo,
    }
    if synth_error:
        result["synth_note"] = f"LLM synthesis issue: {synth_error}"
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
