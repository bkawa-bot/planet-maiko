"""Backfill / refresh violation_description on Learning rows.

Triggered at app startup (in a background thread, so it doesn't block
boot) and exposable as an admin endpoint for manual runs. Picks
learnings that need work — never had a description, OR have accumulated
enough new signals since last gen to warrant a refreshed description —
and processes them sequentially.

Per-learning cost: one Haiku call (~$0.001) + one embedding call (free
locally, ~$0.00001 via API). For a 300-rule corpus, the full backfill
is ~$0.30 + ~30s if local-embedded, or ~$0.30 + ~2 min if going through
an API. Sequential because we don't want to burst the LLM API; this is
a one-shot maintenance task, not a hot path.
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# How many new signals a Learning must have accumulated since its last
# violation_description generation before we re-generate. 5 is a soft
# threshold — small enough to keep descriptions current, large enough
# to avoid hammering the LLM after every signal.
SIGNAL_REGEN_THRESHOLD = 5


def _learnings_needing_work(force=False):
    """Return all active learnings that need their description
    generated or refreshed.

    When `force=True`, returns every active learning — useful when the
    prompt has changed and existing descriptions are stale relative to
    the new framing (e.g. switched from violation-pattern descriptions
    to scenario descriptions). Costs N Haiku calls regardless of
    current state, so use sparingly.
    """
    from planet_maiko.models.learning import Learning

    if force:
        return list(Learning.query.filter_by(status="active").all())

    candidates = []
    for l in Learning.query.filter_by(status="active").all():
        if not l.violation_description or not l.violation_embedding:
            candidates.append(l)
            continue
        # Refresh trigger: signal count has grown enough since last gen
        # that the description should incorporate the new evidence.
        last_gen_count = l.violation_description_signal_count or 0
        current_count = l.signal_count or 0
        if current_count - last_gen_count >= SIGNAL_REGEN_THRESHOLD:
            candidates.append(l)
    return candidates


def _process_one(learning):
    """Generate + embed + persist for a single Learning. Returns True
    on success, False on any skip or failure (logged inside)."""
    from planet_maiko.brain.learning.intent_extraction import (
        generate_violation_description,
    )
    from planet_maiko.brain.learning.embeddings import (
        embed_text,
        embedding_model_name,
    )
    from planet_maiko.database import db

    description = generate_violation_description(learning)
    if not description:
        return False

    embedding = embed_text(description)
    if embedding is None:
        logger.warning(
            f"[violation-backfill] Learning #{learning.id}: "
            f"embedding backend unavailable, storing description without embedding"
        )

    try:
        learning.violation_description = description
        learning.violation_embedding = embedding
        learning.violation_description_generated_at = datetime.now(timezone.utc)
        learning.violation_description_signal_count = learning.signal_count or 0
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"[violation-backfill] DB commit failed for #{learning.id}: {e}")
        return False

    logger.info(
        f"[violation-backfill] Learning #{learning.id} '{learning.rule[:60]}…' — "
        f"description ready ({embedding_model_name() or 'no-embedding'})"
    )
    return True


def backfill_violation_descriptions(force=False):
    """Process every active Learning that needs description work.
    Runs synchronously — call from a background thread for app startup.

    When `force=True`, regenerates EVERY active learning's description,
    even ones already populated. Use after a prompt change (or when
    you want to refresh against richer evidence accumulated since the
    last gen). Costs ~$0.001 per rule; for 300 rules that's ~$0.30.

    Returns dict with counts.
    """
    work = _learnings_needing_work(force=force)
    total = len(work)
    if not total:
        logger.info("[violation-backfill] All active learnings already have current descriptions")
        return {"processed": 0, "succeeded": 0, "failed": 0, "total": 0}

    mode = "force-regen" if force else "incremental"
    logger.info(f"[violation-backfill] Processing {total} learnings ({mode})")
    succeeded = 0
    failed = 0
    for i, learning in enumerate(work, 1):
        try:
            ok = _process_one(learning)
            if ok:
                succeeded += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            logger.warning(f"[violation-backfill] Unexpected error on #{learning.id}: {e}")
        if i % 10 == 0 or i == total:
            logger.info(
                f"[violation-backfill] progress {i}/{total} "
                f"(succeeded={succeeded}, failed={failed})"
            )

    logger.info(
        f"[violation-backfill] Done. Succeeded: {succeeded}, Failed: {failed}, Total: {total}"
    )
    return {"processed": total, "succeeded": succeeded, "failed": failed, "total": total}


def backfill_in_background(app, force=False):
    """Kick off backfill on a daemon thread so app startup isn't
    blocked. Each per-learning call is a Claude+embedding round-trip
    that can take 2-5 seconds; for 300 rules that's potentially
    20+ minutes — way too long to gate boot on.

    Pass force=True to refresh every description, regardless of
    current state — needed after a prompt change."""
    def _runner():
        with app.app_context():
            try:
                backfill_violation_descriptions(force=force)
            except Exception as e:
                logger.warning(f"[violation-backfill] background runner crashed: {e}")

    thread = threading.Thread(
        target=_runner, daemon=True, name="violation-backfill"
    )
    thread.start()
    logger.info(
        f"[violation-backfill] kicked off on background thread"
        f"{' (force-regen)' if force else ''}"
    )
