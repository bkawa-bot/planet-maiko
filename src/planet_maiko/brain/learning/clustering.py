"""Semantic clustering of Learnings.

The prefix-based aggregation in processor._make_aggregation_key only
catches rules that share their first 80 characters. "Always handle null
with Optional.orElseThrow" and "Null check missing — wrap in Optional"
end up as separate Learnings even though they're the same rule.

This module fixes that by asking Claude to group semantically-equivalent
Learnings and picking a canonical rule per cluster. Duplicates are
merged in the DB: the highest-confidence row wins, every duplicate
Learning's signals get re-pointed to the winner (so code examples
follow), and the losing rows get dismissed.

One call per category (we never try to merge a null_safety rule with
a testing rule — that'd be noise). Batches of ~40 per call, with a
soft limit so extremely busy categories don't blow out the context.
"""

import json
import logging
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.learning import Learning
from planet_maiko.models.signal import Signal

logger = logging.getLogger(__name__)

# Keep batches small enough that Claude reasons about each rule
# carefully instead of skimming. Empirically ~40 works well for short
# rule texts; bigger batches cluster less precisely.
BATCH_SIZE = 40

# Don't try to cluster ultra-rare learnings that haven't graduated yet
# unless the caller explicitly asks for it — they're still volatile.
DEFAULT_STATUSES = ("active", "pending")


CLUSTER_PROMPT = """You are deduping a list of short coding rules.

Group rules that say essentially the SAME thing, even if the wording
differs. Rules with different scope, severity, or category should NOT
be merged — only merge true synonyms.

For each cluster you identify, pick a canonical rule text: the clearest,
most actionable one-sentence version. Prefer existing wording when
possible; rewrite only if the existing options are unclear.

Input: a JSON array of {{id, rule, category}} objects.

Return ONLY valid JSON matching this schema. No markdown fencing, no
commentary:
{{
  "clusters": [
    {{"canonical": "...", "member_ids": [1, 7, 12]}},
    {{"canonical": "...", "member_ids": [3]}},
    ...
  ]
}}

Rules:
- Every input id MUST appear in exactly one cluster.
- Clusters of size 1 (no duplicates) are allowed and expected.
- Pick the canonical so it reads like a rule a linter would print.

Rules to cluster:
{rules_json}
"""


def _call_cluster_llm(rules):
    """Send a batch of rules to Claude and return the clusters list.

    Returns [] on any failure — callers treat "no clustering found" as
    "leave things alone", which is the safe default.
    """
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    runtime = _get_runtime()
    if not runtime.is_available():
        logger.warning("[clustering] Brain runtime unavailable")
        return []

    payload = [{"id": r["id"], "rule": r["rule"], "category": r["category"]} for r in rules]
    prompt = CLUSTER_PROMPT.format(rules_json=json.dumps(payload, indent=2))

    db.session.close()
    result = runtime.send_json(prompt, timeout=120, model=resolve_model("classify"))
    parsed = result.get("parsed") if isinstance(result, dict) else None
    if not isinstance(parsed, dict):
        logger.warning(f"[clustering] LLM did not return a JSON object: {result.get('error') if isinstance(result, dict) else 'no response'}")
        return []
    clusters = parsed.get("clusters")
    if not isinstance(clusters, list):
        return []
    return clusters


def _merge_cluster(canonical_text, member_ids):
    """Merge a set of Learnings into one keeper.

    The keeper is the highest-confidence member (tie-broken by earliest
    created_at). Its rule text is replaced by the canonical. Every other
    member's Signals are re-pointed to the keeper via Signal.learning_id,
    the duplicate Learnings are marked dismissed so they stop showing up
    without losing the audit trail.

    Returns (keeper_id, merged_away_count).
    """
    members = Learning.query.filter(Learning.id.in_(member_ids)).all()
    if not members:
        return None, 0
    if len(members) == 1:
        # Canonical rewrite without a merge — still valuable.
        keeper = members[0]
        if canonical_text and canonical_text.strip() and canonical_text != keeper.rule:
            keeper.rule = canonical_text.strip()
        return keeper.id, 0

    # Highest confidence wins; ties broken by earliest creation so we
    # preserve the most-settled rule when scores match.
    members.sort(
        key=lambda m: (-(m.confidence or 0), m.created_at or datetime.min),
    )
    keeper = members[0]
    losers = members[1:]

    if canonical_text and canonical_text.strip():
        keeper.rule = canonical_text.strip()

    merged_signals = 0
    for loser in losers:
        # Move the loser's signals onto the keeper so their code examples
        # contribute to training under the canonical rule.
        loser_signals = Signal.query.filter_by(learning_id=loser.id).all()
        for sig in loser_signals:
            sig.learning_id = keeper.id
            merged_signals += 1

        # Track the merge on the keeper's signal_count so graduation
        # math stays consistent.
        keeper.signal_count = (keeper.signal_count or 0) + (loser.signal_count or 0)
        keeper.confidence = min(1.0, (keeper.confidence or 0) + 0.05 * (loser.signal_count or 1))

        loser.status = "dismissed"
        loser.updated_at = datetime.now(timezone.utc)

    keeper.updated_at = datetime.now(timezone.utc)
    logger.info(
        f"[clustering] Merged {len(losers)} learning(s) into #{keeper.id}: "
        f"'{keeper.rule[:60]}' (+{merged_signals} signals)"
    )
    return keeper.id, len(losers)


def cluster_learnings(statuses=DEFAULT_STATUSES, batch_size=BATCH_SIZE, on_progress=None):
    """Run semantic clustering across eligible Learnings, category by
    category. Merges duplicates in the DB.

    Args:
        statuses: iterable of Learning.status values to include.
        batch_size: how many learnings per LLM call. Smaller = more
            precise but slower.
        on_progress: optional callback (current_category: str,
            processed: int, total: int) -> None.

    Returns:
        dict with counts: {clusters_processed, learnings_merged,
                           categories_scanned, skipped}
    """
    learnings = (
        Learning.query
        .filter(Learning.status.in_(list(statuses)))
        # Skip placeholder-category signals that still haven't been
        # classified — clustering those is noise.
        .filter(Learning.category != "pattern")
        .all()
    )

    # Group by category (we never cluster across categories).
    by_category = {}
    for l in learnings:
        by_category.setdefault(l.category, []).append(l)

    results = {
        "clusters_processed": 0,
        "learnings_merged": 0,
        "categories_scanned": 0,
        "skipped": 0,
    }

    total = len(learnings)
    processed = 0

    for category, members in by_category.items():
        results["categories_scanned"] += 1
        # Nothing to dedup if only one rule in the category.
        if len(members) < 2:
            processed += len(members)
            results["skipped"] += len(members)
            if on_progress:
                on_progress(category, processed, total)
            continue

        # Process this category in batches. Each batch is clustered
        # independently; duplicates spanning batches aren't merged in
        # a single pass but will be on the next run.
        for start in range(0, len(members), batch_size):
            chunk = members[start:start + batch_size]
            rules_input = [
                {"id": l.id, "rule": (l.rule or "").strip(), "category": l.category}
                for l in chunk if (l.rule or "").strip()
            ]
            if len(rules_input) < 2:
                processed += len(chunk)
                results["skipped"] += len(chunk)
                continue

            clusters = _call_cluster_llm(rules_input)
            if not clusters:
                processed += len(chunk)
                results["skipped"] += len(chunk)
                if on_progress:
                    on_progress(category, processed, total)
                continue

            chunk_ids = {l.id for l in chunk}
            for cluster in clusters:
                member_ids = [
                    mid for mid in (cluster.get("member_ids") or [])
                    if isinstance(mid, int) and mid in chunk_ids
                ]
                if not member_ids:
                    continue
                canonical = (cluster.get("canonical") or "").strip()
                _, merged_away = _merge_cluster(canonical, member_ids)
                results["clusters_processed"] += 1
                results["learnings_merged"] += merged_away

            db.session.commit()
            processed += len(chunk)
            if on_progress:
                on_progress(category, processed, total)

    logger.info(
        f"[clustering] Scanned {total} learning(s) across "
        f"{results['categories_scanned']} categor(ies); "
        f"merged {results['learnings_merged']} duplicate(s) "
        f"across {results['clusters_processed']} cluster(s)."
    )
    return results
