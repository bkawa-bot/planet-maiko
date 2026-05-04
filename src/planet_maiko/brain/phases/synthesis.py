"""Phases 3.8 + 4: signal synthesis + learning aggregation.

  - synthesis: drain the queue of unsynthesized pr_comment signals
  - learning: cluster signals into learnings, dedupe drifted categories
"""

import logging

logger = logging.getLogger(__name__)


def _phase_synthesis():
    """Phase 3.8: Self-healing synthesis.

    Drains the queue of synthesized=False pr_comment signals one small
    batch at a time. Transient LLM failures during a backfill (timeout,
    malformed JSON) used to leave signals orphaned — stuck forever
    because nothing else re-synthesized them. This phase retries them
    on every cycle tick until the queue is empty.

    Capped at one batch (40 signals) per tick so the cycle stays
    snappy even when there's a big backlog.
    """
    try:
        from planet_maiko.brain.learning.synthesizer import (
            synthesize_unsynthesized_signals, BATCH_SIZE,
        )
        return synthesize_unsynthesized_signals(max_signals=BATCH_SIZE)
    except Exception as e:
        logger.warning(f"[cycle] Synthesis phase error: {e}")
        return {"found": 0, "processed": 0, "synthesized": 0, "error": str(e)}


def _phase_learning():
    """Phase 4: Aggregate feedback signals into learnings, then drift-
    dedupe the categories we just touched.

    Between-cycle duplicates happen when two signals in different
    cycles each create a new Learning with similar content (e.g.
    "prefer X over Y" and "always use X instead of Y"). The attach
    step doesn't catch these because each batch only sees its own
    Learnings at the moment it ran.

    Event-triggered dedupe: we re-cluster only the categories that
    actually changed this tick, so quiet cycles cost nothing. If no
    new signals came in, this phase is a single cheap filter query.
    """
    from planet_maiko.brain.learning.clustering import (
        cluster_signals_into_learnings, cluster_learnings,
    )
    result = cluster_signals_into_learnings()
    touched = result.get("touched_categories") or []
    if touched:
        drift = cluster_learnings(categories=touched)
        result["drift"] = drift
    return result
