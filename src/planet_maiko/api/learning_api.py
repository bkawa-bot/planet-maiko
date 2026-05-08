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
        limit — default 1000, cap 5000. Big backfill queues need to
        stay visible in one page; clustering itself is batched server-
        side so the UI doesn't have to.
    """
    category = request.args.get("category")
    source_type = request.args.get("source_type")
    aggregated = request.args.get("aggregated")
    synthesized = request.args.get("synthesized")
    limit = min(int(request.args.get("limit", 1000)), 5000)

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

def _actual_signal_counts(learning_ids):
    """Return {learning_id: actual linked-signal count} for the given
    ids. Used to override the cached Learning.signal_count at read
    time — the column is indexed + fast to write, but sundry code
    paths (pack_insights, clustering merges, old signal prunes) have
    drifted it out of sync with reality enough times that trusting
    the cache here leads to the classic "card says 2 signals, click
    shows zero" UX bug. Computing from the Signal table costs one
    extra query on the list endpoint and is a no-op on singletons.
    """
    from planet_maiko.models.signal import Signal
    from sqlalchemy import func
    if not learning_ids:
        return {}
    rows = (
        Signal.query
        .with_entities(Signal.learning_id, func.count(Signal.id))
        .filter(Signal.learning_id.in_(learning_ids))
        .group_by(Signal.learning_id)
        .all()
    )
    return dict(rows)


@learning_bp.route("/learnings", methods=["GET"])
def list_learnings():
    """List learnings, optionally filtered by status or category, with pagination.

    Cap is generous (5000) because the Brain page wants the whole set
    in one response — 900-2000 learnings is normal once a team's been
    using the system for a while, and clustering happens server-side
    in batches so the UI doesn't page through anything besides display.

    Default behavior excludes 'incubating' learnings (1-signal auto-
    created rules waiting for a second corroborating signal) so the
    approval queue isn't drowned in noise. Pass include_incubating=true
    or status=incubating explicitly to surface them.
    """
    status = request.args.get("status")
    category = request.args.get("category")
    include_incubating = request.args.get("include_incubating", "").lower() == "true"
    limit = min(int(request.args.get("limit", 500)), 5000)
    offset = int(request.args.get("offset", 0))

    query = Learning.query
    if status:
        query = query.filter_by(status=status)
    elif not include_incubating:
        query = query.filter(Learning.status != "incubating")
    if category:
        query = query.filter_by(category=category)

    learnings = query.order_by(Learning.confidence.desc()).limit(limit).offset(offset).all()
    counts = _actual_signal_counts([l.id for l in learnings])
    out = []
    for l in learnings:
        d = l.to_dict()
        d["signal_count"] = counts.get(l.id, 0)
        out.append(d)
    return jsonify(out)


@learning_bp.route("/learnings/<int:learning_id>", methods=["GET"])
def get_learning(learning_id):
    """Get a learning with its signals. The returned signal_count is
    computed from actual linked rows, not the cached column — so the
    number in the row always matches the length of the signals list
    below, no matter what drift exists in the cache."""
    learning = db.get_or_404(Learning, learning_id)
    signals = list(learning.signals)
    data = learning.to_dict()
    data["signal_count"] = len(signals)
    data["signals"] = [s.to_dict() for s in signals]
    # Best-effort write-back to heal the cache in the background —
    # keeps indexed queries (list filters, stats) honest.
    if learning.signal_count != len(signals):
        learning.signal_count = len(signals)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
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
    """Edit a learning's rule text, category, scope, or is_global flag.

    Auto-promotion (seen across 3+ repos) already flips is_global, but
    users sometimes want to mark a rule as global up front — e.g. team
    conventions that apply everywhere even when only one repo has
    surfaced them. Accept is_global here; when true, clear scope_repo
    since the two contradict.

    On rule-text edit: invalidate the cached scenario description +
    embedding so the next backfill regenerates them. Without this, an
    edited rule would keep retrieving against its OLD scenario text —
    silently degrading retrieval quality. The 5-signal regen threshold
    in violation_backfill won't catch text-only edits since they don't
    change signal_count.
    """
    learning = db.get_or_404(Learning, learning_id)
    data = request.get_json()
    rule_changed = False
    if "rule" in data:
        new_rule = (data["rule"] or "").strip()
        if new_rule and new_rule != (learning.rule or "").strip():
            learning.rule = new_rule
            rule_changed = True
    if "category" in data:
        learning.category = data["category"]
    if "scope_repo" in data:
        learning.scope_repo = data["scope_repo"]
    if "scope_language" in data:
        learning.scope_language = data["scope_language"]
    if "is_global" in data:
        learning.is_global = bool(data["is_global"])
        if learning.is_global:
            learning.scope_repo = None

    if rule_changed:
        learning.violation_description = None
        learning.violation_embedding = None
        learning.violation_description_generated_at = None
        learning.violation_description_signal_count = None

    db.session.commit()

    # Kick the backfill on a daemon thread so the regen happens now
    # rather than at next boot. Cheap (~$0.001 + a few seconds for one
    # rule). The backfill itself only processes rules whose description
    # is missing OR has accumulated +5 signals — so this is a no-op
    # when nothing else is stale.
    if rule_changed:
        from flask import current_app
        from planet_maiko.brain.learning.violation_backfill import (
            backfill_in_background,
        )
        try:
            backfill_in_background(current_app._get_current_object())
        except Exception as e:
            # Don't block the edit on a backfill kickoff failure — the
            # next boot's startup backfill will pick it up regardless.
            import logging
            logging.getLogger(__name__).warning(
                f"[learnings/edit] background backfill kickoff failed: {e}"
            )

    return jsonify(learning.to_dict())


@learning_bp.route("/learnings/bulk-dismiss", methods=["POST"])
def bulk_dismiss_pending():
    """Dismiss pending learnings in bulk by quality threshold.

    Used to drain a backlog of thin / old pending learnings the user
    isn't going to review individually. Acts only on status="pending"
    rows (active rules are protected — the user already approved them)
    and only on rows matching ALL provided filters.

    Body:
      max_signal_count: int (default 1) — only dismiss learnings with
        signal_count <= this number. Default catches one-signal
        singletons that are usually noise.
      older_than_days: int (default 14) — only dismiss learnings
        created more than N days ago, so anything fresh stays in the
        queue for review.
      dry_run: bool (default false) — when true, return the count of
        matching rows without changing anything. Lets the UI show a
        preview before the user confirms.

    Returns:
      {count: int, dismissed: int, sample: [{id, rule, signal_count,
       created_at}, ...]}
      `count` is always set; `dismissed` is 0 on dry runs.
    """
    from datetime import datetime, timezone, timedelta

    data = request.get_json(silent=True) or {}
    max_signal_count = max(0, int(data.get("max_signal_count", 1)))
    older_than_days = max(0, int(data.get("older_than_days", 14)))
    dry_run = bool(data.get("dry_run", False))

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    q = (
        Learning.query
        .filter(Learning.status == "pending")
        .filter(Learning.signal_count <= max_signal_count)
        .filter(Learning.created_at < cutoff)
    )
    count = q.count()

    sample = [
        {
            "id": l.id,
            "rule": l.rule,
            "signal_count": l.signal_count or 0,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in q.order_by(Learning.created_at.asc()).limit(10).all()
    ]

    if dry_run:
        return jsonify({"count": count, "dismissed": 0, "sample": sample})

    # Bulk update the status — much faster than iterating, and
    # synchronize_session=False is fine because we don't read the rows
    # in the same session afterwards.
    dismissed = (
        q.update(
            {"status": "dismissed"},
            synchronize_session=False,
        )
    )
    db.session.commit()
    return jsonify({"count": count, "dismissed": dismissed, "sample": sample})


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


# Module-level cluster job state. Single-job (no concurrency assumed —
# clicking Cluster while one is running just returns the current state).
# The brain cycle's auto-clustering doesn't update this dict; it's
# scoped to the user-triggered manual sweeps so the Brain page progress
# bar stays focused on "the run I just kicked off."
_cluster_progress = {
    "running": False,
    "current_category": None,
    "processed": 0,
    "total": 0,
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
}
_cluster_lock = None  # threading.Lock, lazy-created so import-time stays cheap


def _ensure_cluster_lock():
    global _cluster_lock
    if _cluster_lock is None:
        import threading
        _cluster_lock = threading.Lock()
    return _cluster_lock


def _cluster_run(app):
    """Background runner for the manual cluster sweep. Updates
    _cluster_progress as cluster_learnings() walks each category +
    batch. on_progress is invoked once per (category, batch) tick."""
    from datetime import datetime, timezone
    from planet_maiko.brain.learning.clustering import cluster_learnings

    def on_progress(category, processed, total):
        _cluster_progress["current_category"] = category
        _cluster_progress["processed"] = processed
        _cluster_progress["total"] = total

    with app.app_context():
        try:
            result = cluster_learnings(on_progress=on_progress)
            _cluster_progress["result"] = result
        except Exception as e:
            logger.exception("[cluster] background run failed")
            _cluster_progress["error"] = str(e)
        finally:
            _cluster_progress["running"] = False
            _cluster_progress["finished_at"] = datetime.now(timezone.utc).isoformat()
            _cluster_progress["current_category"] = None


@learning_bp.route("/learnings/cluster", methods=["POST"])
def cluster_learnings_endpoint():
    """Kick off a full-sweep clustering pass on a background thread.

    Returns 202 immediately so the frontend can render a progress bar
    via /learnings/cluster/status polling. If a sweep is already
    running, returns the existing progress instead of starting a
    second one.
    """
    from datetime import datetime, timezone
    import threading
    from flask import current_app

    lock = _ensure_cluster_lock()
    with lock:
        if _cluster_progress["running"]:
            return jsonify({"already_running": True, **_cluster_progress}), 202
        # Reset state for a fresh run.
        _cluster_progress.update({
            "running": True,
            "current_category": None,
            "processed": 0,
            "total": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "result": None,
            "error": None,
        })

    app = current_app._get_current_object()
    thread = threading.Thread(target=_cluster_run, args=(app,), daemon=True)
    thread.start()
    return jsonify({"started": True, **_cluster_progress}), 202


@learning_bp.route("/learnings/cluster/status", methods=["GET"])
def cluster_status():
    """Poll the current/last clustering sweep's progress. Returns the
    same dict shape regardless of state — the `running` flag tells
    the caller whether to keep polling."""
    return jsonify(_cluster_progress)


