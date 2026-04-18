import logging

from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.signal import Signal
from planet_maiko.models.learning import Learning

logger = logging.getLogger(__name__)

learning_bp = Blueprint("learning", __name__)


# --- Signals ---

@learning_bp.route("/signals", methods=["GET"])
def list_signals():
    """List signals, optionally filtered.

    Query params:
        category, source_type, aggregated, synthesized — all optional
        filters. `synthesized=false` is how the Knowledge tab surfaces
        raw signals that haven't made it through LLM synthesis yet.
        limit — default 500 (up from 100) so big backfill queues stay
        visible in one page.
    """
    category = request.args.get("category")
    source_type = request.args.get("source_type")
    aggregated = request.args.get("aggregated")
    synthesized = request.args.get("synthesized")
    limit = min(int(request.args.get("limit", 500)), 2000)

    query = Signal.query
    if category:
        query = query.filter_by(category=category)
    if source_type:
        query = query.filter_by(source_type=source_type)
    if aggregated is not None:
        query = query.filter_by(aggregated=aggregated.lower() == "true")
    if synthesized is not None:
        query = query.filter_by(synthesized=synthesized.lower() == "true")

    signals = query.order_by(Signal.created_at.desc()).limit(limit).all()
    return jsonify([s.to_dict() for s in signals])


@learning_bp.route("/signals/count", methods=["GET"])
def count_signals():
    """Fast count of signals matching the given filters.

    Same filter params as /signals (category, source_type, aggregated,
    synthesized). The Knowledge tab uses this for the Unsynthesized
    badge so the number is the real queue size, not the row-limited
    list length.
    """
    category = request.args.get("category")
    source_type = request.args.get("source_type")
    aggregated = request.args.get("aggregated")
    synthesized = request.args.get("synthesized")

    query = Signal.query
    if category:
        query = query.filter_by(category=category)
    if source_type:
        query = query.filter_by(source_type=source_type)
    if aggregated is not None:
        query = query.filter_by(aggregated=aggregated.lower() == "true")
    if synthesized is not None:
        query = query.filter_by(synthesized=synthesized.lower() == "true")

    return jsonify({"count": query.count()})


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
        # Manual / API-created signals carry an explicit category —
        # no LLM synthesis needed.
        synthesized=True,
    )
    db.session.add(signal)
    db.session.commit()
    return jsonify(signal.to_dict()), 201


# --- Learnings ---

@learning_bp.route("/learnings", methods=["GET"])
def list_learnings():
    """List learnings, optionally filtered by status or category, with pagination."""
    status = request.args.get("status")
    category = request.args.get("category")
    limit = min(int(request.args.get("limit", 200)), 500)
    offset = int(request.args.get("offset", 0))

    query = Learning.query
    if status:
        query = query.filter_by(status=status)
    if category:
        query = query.filter_by(category=category)

    learnings = query.order_by(Learning.confidence.desc()).limit(limit).offset(offset).all()
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
    """Manually run synthesis + clustering on the current queue.

    Used by the "Synthesize Now" button on the Unsynthesized tab — same
    work the cycle's synthesis + learning phases do, but kicked off
    immediately so the user doesn't wait for the next tick.

    Caps at `batch_size` (default 50) signals per call so one click
    doesn't hang the browser for minutes on a big backlog. Click
    again to drain more, or let the cycle phase catch up in the
    background.
    """
    from planet_maiko.brain.learning.synthesizer import synthesize_unsynthesized_signals
    from planet_maiko.brain.learning.clustering import cluster_signals_into_learnings

    data = request.get_json(silent=True) or {}
    batch_size = int(data.get("batch_size", 50))

    db.session.close()

    synth = synthesize_unsynthesized_signals(max_signals=batch_size)
    cluster = cluster_signals_into_learnings()

    # Count what's still waiting so the UI can tell the user whether
    # another click (or just patience with the cycle) is needed.
    from planet_maiko.models.signal import Signal
    remaining = Signal.query.filter_by(
        source_type="pr_comment", synthesized=False,
    ).count()

    return jsonify({
        "synthesized": synth.get("synthesized", 0),
        "dropped_junk": synth.get("dropped_junk", 0),
        "processed": synth.get("processed", 0),
        "new_learnings": cluster.get("new_learnings", 0),
        "graduated": cluster.get("graduated", 0),
        "remaining": remaining,
    })


def _run_backfill_job(app, limit, repo):
    """The actual backfill work — runs in a background thread.

    Updates the shared progress dict as it moves through fetch → synthesize
    → aggregate phases, so the UI can poll /learnings/backfill/status.
    """
    from planet_maiko.brain.learning.bootstrap import (
        bootstrap_from_prs, update_backfill_progress,
    )
    from planet_maiko.brain.learning.processor import process_signals
    from datetime import datetime, timezone

    with app.app_context():
        try:
            # Phase 1: fetch PR comments
            update_backfill_progress(phase="fetching")
            repos = [repo] if repo else None
            bootstrap_result = bootstrap_from_prs(limit=limit, repos=repos)
            signals_created = bootstrap_result["total_created"]
            per_repo = bootstrap_result["per_repo"]

            synthesized = 0
            synth_error = None

            if signals_created > 0:
                # Phase 2: LLM synthesis. Delegates to the shared
                # synthesizer so the cycle's self-healing phase runs the
                # exact same logic on a smaller budget per tick.
                update_backfill_progress(phase="synthesizing")
                try:
                    from planet_maiko.brain.learning.synthesizer import synthesize_unsynthesized_signals
                    synth_result = synthesize_unsynthesized_signals(
                        on_progress=lambda n: update_backfill_progress(synthesized=n),
                    )
                    synthesized = synth_result["synthesized"]
                    if synth_result.get("error"):
                        synth_error = synth_result["error"]
                except Exception as e:
                    synth_error = str(e)

                # Phase 3: cluster signals directly into Learnings.
                # This replaces the old prefix-based aggregation +
                # separate dedup pass — one semantic call that matches
                # each new signal against existing rules or starts a
                # new cluster.
                update_backfill_progress(phase="clustering")
                from planet_maiko.brain.learning.clustering import cluster_signals_into_learnings
                learning_results = cluster_signals_into_learnings()
                update_backfill_progress(
                    new_learnings=learning_results.get("new_learnings", 0),
                    graduated=learning_results.get("graduated", 0),
                )
                cluster_results = {"learnings_merged": 0}
            else:
                learning_results = {"new_learnings": 0, "graduated": 0}
                cluster_results = {"learnings_merged": 0}

            summary = {
                "signals_created": signals_created,
                "synthesized": synthesized,
                "new_learnings": learning_results.get("new_learnings", 0),
                "graduated": learning_results.get("graduated", 0),
                "learnings_merged": cluster_results.get("learnings_merged", 0),
                "per_repo": per_repo,
            }
            if synth_error:
                summary["synth_note"] = f"LLM synthesis issue: {synth_error}"

            update_backfill_progress(
                phase="done",
                running=False,
                finished_at=datetime.now(timezone.utc).isoformat(),
                result=summary,
            )
        except Exception as e:
            update_backfill_progress(
                phase="error",
                running=False,
                error=str(e)[:300],
                finished_at=datetime.now(timezone.utc).isoformat(),
            )


@learning_bp.route("/learnings/backfill", methods=["POST"])
def backfill_learnings():
    """Kick off a PR backfill asynchronously. Poll /learnings/backfill/status."""
    import threading
    from datetime import datetime, timezone
    from flask import current_app
    from planet_maiko.brain.learning.bootstrap import (
        get_backfill_progress, reset_backfill_progress, update_backfill_progress,
    )

    progress = get_backfill_progress()
    if progress["running"]:
        return jsonify({"error": "A backfill is already running", "progress": progress}), 409

    data = request.get_json(silent=True) or {}
    # Default to no cap — the inline-only flow fetches via one paginated
    # gh api call per repo, so the "per-repo limit" knob is mostly a
    # sanity cap for users with very large histories.
    limit = data.get("limit")
    repo = data.get("repo")

    reset_backfill_progress()
    update_backfill_progress(
        running=True,
        phase="fetching",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_run_backfill_job, args=(app, limit, repo), daemon=True,
    )
    thread.start()
    return jsonify({"started": True})


@learning_bp.route("/learnings/backfill/status", methods=["GET"])
def backfill_status():
    """Poll the active backfill job's progress."""
    from planet_maiko.brain.learning.bootstrap import get_backfill_progress
    return jsonify(get_backfill_progress())


@learning_bp.route("/learnings/cluster", methods=["POST"])
def cluster_learnings_endpoint():
    """Run semantic clustering over the active learning pool.

    Useful to merge duplicates that slipped through the prefix-based
    aggregator (e.g. "handle null with Optional" and "null check missing"
    that ended up as separate Learnings). Synchronous — one LLM call per
    batch of ~40 learnings per category.
    """
    from planet_maiko.brain.learning.clustering import cluster_learnings
    try:
        results = cluster_learnings()
        return jsonify(results)
    except Exception as e:
        logger.exception("[cluster] failed")
        return jsonify({"error": str(e)}), 500


@learning_bp.route("/learnings/brief", methods=["GET"])
def learning_brief():
    """Compile active learnings into a brief for agents.

    Returns the rendered markdown prose plus the structured Learning
    list the prose was built from, so external orchestrators can
    either drop the prose into their own CLAUDE.md or format the
    rules themselves.

    Optional query params: repo, language (to scope the brief).

    Response 200:
        {
          "brief": "<markdown>",
          "learnings": [ ... Learning.to_dict() ... ]
        }
    """
    from planet_maiko.brain.learning.processor import compile_brief, select_brief_learnings
    repo = request.args.get("repo")
    language = request.args.get("language")
    brief = compile_brief(repo=repo, language=language)
    selected = select_brief_learnings(repo=repo, language=language)
    return jsonify({
        "brief": brief,
        "learnings": [l.to_dict() for l in selected],
    })
