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
    """Manually synthesize pending pattern signals AND learnings via LLM."""
    from planet_maiko.brain.learning.classifier import (
        classify_unclassified_signals, classify_pattern_learnings
    )
    from planet_maiko.brain.learning.clustering import cluster_signals_into_learnings

    data = request.get_json(silent=True) or {}
    batch_size = data.get("batch_size", 50)

    # Release DB before LLM call
    db.session.close()

    # First: classify any unaggregated signals
    classified_signals = classify_unclassified_signals(batch_size=batch_size)
    learning_results = cluster_signals_into_learnings()

    # Then: reclassify any existing pattern-category learnings
    classified_learnings = classify_pattern_learnings(batch_size=batch_size)

    return jsonify({
        "classified_signals": classified_signals,
        "classified_learnings": classified_learnings,
        "new_learnings": learning_results.get("new_learnings", 0),
        "graduated": learning_results.get("graduated", 0),
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
                # Phase 2: LLM synthesis — batch through EVERY unsynthesized
                # pr_comment signal (not just the first 20). Keeping the
                # batch size modest so the model stays precise on each.
                SYNTH_BATCH = 40
                update_backfill_progress(phase="synthesizing")
                try:
                    from planet_maiko.models.signal import Signal as BackfillSignal
                    from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
                    from planet_maiko.agents.routing import resolve_model

                    raw = BackfillSignal.query.filter_by(
                        source_type="pr_comment", aggregated=False
                    ).all()

                    runtime = ClaudeCodeRuntime()
                    model = resolve_model("classify")

                    for start in range(0, len(raw), SYNTH_BATCH):
                        batch = raw[start:start + SYNTH_BATCH]
                        comments = [
                            f"id={s.id} [{s.repo or 'unknown'}] {s.text[:300]}"
                            for s in batch
                        ]
                        prompt = f"""Synthesize these PR review comments into clean, actionable coding rules.

For each comment, extract the core lesson as a short rule (one sentence).
Also classify each into a category. Echo back the id exactly as given.

Comments:
{chr(10).join(comments)}

Categories: security, error_handling, testing, performance, api_design,
architecture, null_safety, style, naming, docs, pattern, domain_knowledge

Respond as JSON:
{{"rules": [
  {{"id": 1234, "rule": "Always validate input lengths at API boundaries", "category": "security"}},
  ...
]}}"""

                        db.session.close()
                        result = runtime.send_json(prompt, timeout=120, model=model)

                        parsed_rules = (result.get("parsed") or {}).get("rules") if result else None
                        if parsed_rules:
                            # Look up only the signals the LLM actually
                            # returned — refetch is still needed because
                            # db.session.close() invalidated the batch objects
                            # during the long LLM call.
                            from planet_maiko.models.signal import Signal as RefetchSignal
                            returned_ids = [
                                r["id"] for r in parsed_rules
                                if isinstance(r, dict) and isinstance(r.get("id"), int)
                            ]
                            refetched = RefetchSignal.query.filter(
                                RefetchSignal.id.in_(returned_ids)
                            ).all() if returned_ids else []
                            by_id = {s.id: s for s in refetched}
                            for rule_data in parsed_rules:
                                target = by_id.get(rule_data.get("id"))
                                if target is None:
                                    continue
                                target.text = rule_data.get("rule", target.text)
                                target.category = rule_data.get("category", "pattern")
                                synthesized += 1
                            db.session.commit()

                        update_backfill_progress(synthesized=synthesized)
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

    Optional query params: repo, language (to scope the brief)
    """
    from planet_maiko.brain.learning.processor import compile_brief
    repo = request.args.get("repo")
    language = request.args.get("language")
    brief = compile_brief(repo=repo, language=language)
    return jsonify({"brief": brief})
