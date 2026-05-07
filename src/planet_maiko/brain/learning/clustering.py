"""Semantic clustering of signals → learnings.

The single semantic pass that turns cleaned signals into Learning rows.
Subsumes what the old processor.py prefix-based aggregation did: for
each batch of unaggregated signals, Claude decides whether each signal
belongs to an existing Learning in the same category or starts a new
cluster. Prefix matching is only a fallback for when the LLM runtime
is unavailable.

Two public entry points:
  * cluster_signals_into_learnings() — the pipeline step called from
    backfill + the brain cycle. Processes unaggregated signals.
  * cluster_learnings() — a cleanup-only pass over already-created
    Learnings (drift collector), kept for manual re-merging.
"""

import json
import logging
import time
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.learning import Learning
from planet_maiko.models.signal import Signal

logger = logging.getLogger(__name__)

# Circuit breaker for the clustering LLM call. If the runtime is
# unavailable, we don't want every brain cycle (and every batch within
# it) to retry and spam the logs. Track the cooldown until-time globally;
# while we're inside that window, skip clustering entirely with a single
# log line per cycle.
_LLM_COOLDOWN_SECONDS = 15 * 60  # 15 minutes
_llm_cooldown_until = 0.0

# Keep batches small enough that Claude reasons about each rule
# carefully instead of skimming. Empirically ~40 works well for short
# rule texts; bigger batches cluster less precisely.
BATCH_SIZE = 40

# Statuses included in the dedup sweep. Includes "incubating" so
# 1-signal-each rules from different cycles can merge with each other
# and promote — without that, two singletons stay frozen as
# incubating forever, never reaching the pending threshold.
DEFAULT_STATUSES = ("active", "pending", "incubating")

# Flip a Learning to is_global=True once its signals have come from
# this many distinct repos. Once global, the rule feeds every repo's
# LoRA training dataset.
GLOBAL_PROMOTE_REPOS = 3


def _maybe_promote_global(learning):
    """Flip is_global=True once signals come from >= GLOBAL_PROMOTE_REPOS
    distinct repos. Idempotent; once promoted, stays promoted."""
    if learning.is_global:
        return False
    # Distinct repos via a straight SELECT DISTINCT on the signals table.
    # Cheap — signal_count per learning is small.
    from sqlalchemy import distinct, func
    repo_count = (
        db.session.query(func.count(distinct(Signal.repo)))
        .filter(Signal.learning_id == learning.id, Signal.repo.isnot(None))
        .scalar() or 0
    )
    if repo_count >= GLOBAL_PROMOTE_REPOS:
        learning.is_global = True
        logger.info(
            f"[clustering] Promoted learning #{learning.id} to global "
            f"({repo_count} distinct repos): {learning.rule[:60]}"
        )
        return True
    return False


CLUSTER_PROMPT = """You are deduping a list of short coding rules, all
in the same category. Group rules that say essentially the SAME thing
even when worded differently — cross-repo duplicates (same rule
observed in different codebases) are expected and should merge so the
rule gets promoted to a global guideline.

For each cluster you identify, pick a canonical rule text: the clearest,
most actionable one-sentence version. Prefer existing wording when
possible; rewrite only if the existing options are unclear.

Input: a JSON array of {{id, rule}} objects.

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
- Don't merge rules that describe genuinely different behaviors.

Rules to cluster:
{rules_json}
"""


def _call_cluster_llm(rules):
    """Send a batch of rules to Claude and return the clusters list.

    Returns [] on any failure — callers treat "no clustering found" as
    "leave things alone", which is the safe default.
    """
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model, resolve_effort

    runtime = _get_runtime()
    if not runtime.is_available():
        logger.warning("[clustering] Brain runtime unavailable")
        return []

    # Category is implied by the batch (cluster_learnings groups by
    # category before calling); not including it in the payload keeps
    # the LLM focused on the semantic-duplicate question.
    payload = [{"id": r["id"], "rule": r["rule"]} for r in rules]
    prompt = CLUSTER_PROMPT.format(rules_json=json.dumps(payload, indent=2))

    # Intentionally do NOT close the session here. An earlier version
    # called db.session.close() to release the connection during the
    # long LLM call — but that detached every Signal / Learning object
    # the caller had in scope, so subsequent assignments like
    # signal.learning_id = learning.id silently failed to persist on
    # commit (learnings landed, their signals never got linked). This
    # is a drift-collector pass on a short queue, connection hold is
    # fine. Keep the session live.
    result = runtime.send_json(
        prompt, timeout=120,
        model=resolve_model("classify"), effort=resolve_effort("classify"),
    )
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
        moved = 0
        for sig in loser_signals:
            sig.learning_id = keeper.id
            moved += 1
            merged_signals += 1

        # Use the actual number of rows moved, not loser.signal_count.
        # The cached count can drift (older clustering bugs inflated
        # it) — if we trust the cache here, the drift propagates onto
        # the keeper and the Knowledge UI ends up saying "N signals"
        # with fewer rows behind it.
        keeper.signal_count = (keeper.signal_count or 0) + moved
        keeper.confidence = min(1.0, (keeper.confidence or 0) + 0.05 * max(moved, 1))

        loser.signal_count = 0
        loser.status = "dismissed"
        loser.updated_at = datetime.now(timezone.utc)

    keeper.updated_at = datetime.now(timezone.utc)

    # After absorbing losers' signals, the keeper may now have signals
    # from 3+ distinct repos → auto-promote to global. Without this,
    # duplicate rules scattered across repos would merge but stay
    # scope-locked to whichever repo the keeper originated from.
    _maybe_promote_global(keeper)

    logger.info(
        f"[clustering] Merged {len(losers)} learning(s) into #{keeper.id}: "
        f"'{keeper.rule[:60]}' (+{merged_signals} signals)"
    )
    return keeper.id, len(losers)


def cluster_learnings(statuses=DEFAULT_STATUSES, batch_size=BATCH_SIZE,
                      on_progress=None, categories=None):
    """Run semantic clustering across eligible Learnings, category by
    category. Merges duplicates in the DB.

    Args:
        statuses: iterable of Learning.status values to include.
        batch_size: how many learnings per LLM call. Smaller = more
            precise but slower.
        on_progress: optional callback (current_category: str,
            processed: int, total: int) -> None.
        categories: optional iterable limiting the scan to these
            category names. Used by the cycle's drift-dedupe phase to
            only re-check categories that got new signals this tick.

    Returns:
        dict with counts: {clusters_processed, learnings_merged,
                           categories_scanned, skipped}
    """
    q = Learning.query.filter(Learning.status.in_(list(statuses)))
    if categories:
        q = q.filter(Learning.category.in_(list(categories)))
    learnings = q.all()

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


# ---------------------------------------------------------------------------
# Signals → Learnings (the main aggregation path)
# ---------------------------------------------------------------------------

SIGNAL_BATCH = 30  # new signals per LLM call

ATTACH_PROMPT = """You are the rule-indexer for a coding guidelines system.

For each NEW_SIGNAL below, choose ONE of:
  - Match an EXISTING_RULE (same idea, even if worded differently) -> cluster with that existing_id.
  - Match a REJECTED_RULE (the user has explicitly dismissed this pattern as not worth tracking) -> put in a "drop" cluster. Don't propose a new rule for it.
  - Neither -> propose a new rule: a short, actionable one-sentence version.

Never merge signals across different ideas. Every NEW_SIGNAL id must
appear in exactly one cluster. Clusters of size 1 are allowed and
common. Be liberal about matching to REJECTED_RULES — if a new signal
is clearly the same pattern (or a near-rephrase) of one the user
dismissed, drop it. The user has told us they don't want that rule.

Category for all items: {category}

EXISTING_RULES (attach to these when possible):
{existing_json}

REJECTED_RULES (the user dismissed these — drop signals matching them):
{rejected_json}

NEW_SIGNALS (place each into a cluster):
{signals_json}

Return ONLY valid JSON matching this schema. No markdown, no commentary.
{{
  "clusters": [
    {{"existing_id": 42,   "canonical": null, "member_ids": [1, 5]}},
    {{"drop": true, "rejected_id": 7, "member_ids": [3]}},
    {{"existing_id": null, "canonical": "Short new rule",
      "member_ids": [4]}}
  ]
}}
"""

# Cap on dismissed rules in the prompt so it doesn't balloon over time.
# Most categories have a handful at most; pulling the latest 50 keeps
# the LLM context small while covering anything the user actually cares
# about. Older dismissals naturally lose relevance.
REJECTED_RULES_LIMIT = 50


def _call_attach_llm(category, existing, signals, rejected=None):
    """Send a batch to Claude and parse clusters.

    Returns (clusters: list[dict], ok: bool). ok=False means caller
    should fall back to prefix-based aggregation for this batch.
    """
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model, resolve_effort

    runtime = _get_runtime()
    if not runtime.is_available():
        return [], False

    existing_payload = [{"id": l.id, "rule": (l.rule or "").strip()} for l in existing]
    rejected_payload = [
        {"id": l.id, "rule": (l.rule or "").strip()}
        for l in (rejected or [])
        if (l.rule or "").strip()
    ]
    signal_payload = [{"id": s.id, "text": (s.text or "").strip()[:300]} for s in signals]

    prompt = ATTACH_PROMPT.format(
        category=category,
        existing_json=json.dumps(existing_payload, indent=2),
        rejected_json=json.dumps(rejected_payload, indent=2),
        signals_json=json.dumps(signal_payload, indent=2),
    )

    # CRITICAL: do not close the session before the LLM call. The
    # caller (cluster_signals_into_learnings) is holding every Signal
    # in `signals` as attached ORM instances and relies on being able
    # to mutate them in the cluster loop after we return
    # (signal.learning_id = learning.id). db.session.close() detaches
    # them — the assignments succeed silently on detached objects and
    # never hit the database, which is why backfilled learnings
    # historically landed with zero linked signals. Connection hold
    # during the 120s LLM call is acceptable; integrity isn't.
    result = runtime.send_json(
        prompt, timeout=120,
        model=resolve_model("classify"), effort=resolve_effort("classify"),
    )
    parsed = result.get("parsed") if isinstance(result, dict) else None
    if not isinstance(parsed, dict):
        return [], False
    clusters = parsed.get("clusters")
    if not isinstance(clusters, list):
        return [], False
    return clusters, True


def cluster_signals_into_learnings():
    """Turn unaggregated signals into Learnings via a single semantic pass.

    For each category, Claude sees both the existing Learnings and the
    new signals that need a home. Signals that match an existing Learning
    attach to it; signals that introduce a genuinely new idea become a
    brand-new Learning.

    If the LLM runtime is unavailable, signals are left in the queue —
    we don't degrade to prefix-matching anymore. Clustering is the
    single source of truth for getting signals into rules; the next
    cycle tick tries again when the runtime comes back.

    Returns a dict with counts (matches the shape process_signals used
    to return so callers don't need to change).
    """
    from planet_maiko.brain.learning.processor import _apply_positive_signal

    # Only cluster signals that have been synthesized (or came from a
    # source that set a real category directly, like CLI feedback).
    # Non-synthesized signals still carry the bootstrap default
    # category, which may be "pattern" but could also just be stale;
    # we wait for synthesis to settle them before aggregation.
    unprocessed = Signal.query.filter_by(aggregated=False, synthesized=True).all()
    waiting_unsynthesized = Signal.query.filter_by(
        aggregated=False, synthesized=False,
    ).count()
    if not unprocessed:
        if waiting_unsynthesized:
            logger.info(
                f"[clustering] {waiting_unsynthesized} signal(s) still waiting for "
                "synthesis — the synthesis cycle phase will retry them"
            )
        return {"processed": 0, "new_learnings": 0, "updated_learnings": 0,
                "graduated": 0, "touched_categories": []}

    # Circuit breaker: if a recent attempt failed because the LLM
    # runtime was unavailable, skip clustering entirely until the
    # cooldown elapses. One log line per cycle instead of one per batch.
    global _llm_cooldown_until
    now = time.time()
    if now < _llm_cooldown_until:
        remaining = int(_llm_cooldown_until - now)
        logger.info(
            f"[clustering] Skipping {len(unprocessed)} signal(s) — "
            f"LLM cooldown active ({remaining}s remaining). "
            f"Signals stay queued for the next cycle."
        )
        return {"processed": 0, "new_learnings": 0, "updated_learnings": 0,
                "graduated": 0, "deferred": len(unprocessed),
                "touched_categories": []}

    counts = {
        "processed": 0,
        "new_learnings": 0,
        "updated_learnings": 0,
        "graduated": 0,
        "deferred": 0,
    }
    if waiting_unsynthesized:
        counts["awaiting_synthesis"] = waiting_unsynthesized
    # Categories we actually modified this pass — the drift-dedupe
    # phase re-clusters just these to catch between-cycle duplicates
    # without re-scanning every category every tick.
    touched = set()

    # Group by category. Junk filtering happens upstream (synthesis
    # marks non-actionable signals and deletes them), so by the time
    # we get here everything is rule-shaped and category is real —
    # "pattern" here is a legitimate LLM-chosen bucket, not the
    # bootstrap placeholder.
    by_category = {}
    for s in unprocessed:
        by_category.setdefault(s.category or "pattern", []).append(s)

    for category, signals in by_category.items():
        existing = Learning.query.filter(
            Learning.category == category,
            Learning.status != "dismissed",
        ).all()
        existing_by_id = {l.id: l for l in existing}
        # Pull the user's recently-dismissed rules in this category so
        # the LLM knows to DROP matching new signals instead of
        # cheerfully re-creating the same pending Learning every cycle.
        # Without this, dismissing a rule was theatre — the next batch
        # of similar signals would resurrect it under a new id.
        rejected = (
            Learning.query
            .filter(Learning.category == category, Learning.status == "dismissed")
            .order_by(Learning.updated_at.desc())
            .limit(REJECTED_RULES_LIMIT)
            .all()
        )

        for start in range(0, len(signals), SIGNAL_BATCH):
            batch = signals[start:start + SIGNAL_BATCH]
            clusters, ok = _call_attach_llm(category, existing, batch, rejected=rejected)

            if not ok:
                # LLM unavailable or returned unparseable output. Leave
                # the batch's signals aggregated=False so the next cycle
                # tries again. Trip the circuit breaker so subsequent
                # batches in this same cycle (and the next 15 min of
                # cycles) skip immediately instead of repeating the
                # same failure for every category × batch. (The outer
                # `global _llm_cooldown_until` declaration up top covers
                # this assignment too — re-declaring it here would be a
                # SyntaxError because the name was already read above.)
                _llm_cooldown_until = time.time() + _LLM_COOLDOWN_SECONDS
                remaining_signals = sum(len(s) for s in by_category.values()) - counts["processed"]
                logger.warning(
                    f"[clustering] LLM unavailable on first batch — tripping "
                    f"cooldown for {_LLM_COOLDOWN_SECONDS // 60}min. "
                    f"{remaining_signals} signal(s) deferred to next cycle."
                )
                counts["deferred"] = counts.get("deferred", 0) + len(batch)
                # Bail out of the whole pass; cooldown handles the rest.
                return counts

            batch_by_id = {s.id: s for s in batch}
            placed = set()
            # Track every Learning whose signals changed this batch so
            # we can run the global-promote check exactly once per
            # learning AFTER the commit releases the write lock. The
            # old path ran it inline per-cluster + per-orphan, which
            # triggered an autoflush on every iteration (the query
            # inside _maybe_promote_global forces pending writes out
            # under the write lock) — one of the two things making
            # clustering tx times show up as "slow tx" warnings.
            batch_new_learnings = []

            for cluster in clusters:
                member_ids = [
                    mid for mid in (cluster.get("member_ids") or [])
                    if isinstance(mid, int) and mid in batch_by_id
                ]
                if not member_ids:
                    continue

                # Drop cluster: signals matched a user-dismissed rule.
                # Mark them aggregated=True so the queue stops carrying
                # them, but DON'T attach to a Learning and DON'T create
                # a new one. The dismissed Learning's signal_count
                # stays accurate to its pre-dismissal state.
                if cluster.get("drop"):
                    for sid in member_ids:
                        sig = batch_by_id[sid]
                        sig.aggregated = True
                        placed.add(sid)
                        counts["processed"] += 1
                        counts["dropped_rejected"] = counts.get("dropped_rejected", 0) + 1
                        touched.add(category)
                    continue

                existing_id = cluster.get("existing_id")
                learning = existing_by_id.get(existing_id) if isinstance(existing_id, int) else None

                if learning is None:
                    # New Learning for this cluster. signal_count /
                    # confidence start at 0; bumped once per member by
                    # _apply_positive_signal below — that's where the
                    # incubating → pending promotion fires once the
                    # second signal lands. Single-member clusters end
                    # at signal_count=1 and stay incubating until a
                    # later signal joins (or the user manually approves).
                    canonical = (cluster.get("canonical") or "").strip()
                    if not canonical:
                        canonical = batch_by_id[member_ids[0]].text[:300]
                    sample = batch_by_id[member_ids[0]]
                    learning = Learning(
                        rule=canonical,
                        category=category,
                        scope_repo=sample.repo,
                        scope_language=sample.language,
                        confidence=0.0,
                        signal_count=0,
                        source="auto",
                        status="incubating",
                        aggregation_key=f"cluster:{category}:{canonical[:60].lower()}",
                        last_signal_at=datetime.now(timezone.utc),
                    )
                    db.session.add(learning)
                    batch_new_learnings.append(learning)
                    counts["new_learnings"] += 1

                for sid in member_ids:
                    sig = batch_by_id[sid]
                    _apply_positive_signal(sig, learning, counts)
                    sig.aggregated = True
                    placed.add(sid)
                    counts["processed"] += 1
                    touched.add(category)

            # Any signal the LLM dropped on the floor — park it as its
            # own new Learning using its own text. Safer than losing it.
            # Starts incubating; will promote to pending if a future
            # signal in a later batch joins this cluster (or the user
            # approves it manually for a one-shot rule).
            for sid, sig in batch_by_id.items():
                if sid in placed:
                    continue
                learning = Learning(
                    rule=sig.text[:300],
                    category=category,
                    scope_repo=sig.repo,
                    scope_language=sig.language,
                    confidence=0.0,
                    signal_count=0,
                    source="auto",
                    status="incubating",
                    aggregation_key=f"cluster:{category}:{sig.text[:60].lower()}",
                    last_signal_at=datetime.now(timezone.utc),
                )
                db.session.add(learning)
                batch_new_learnings.append(learning)
                counts["new_learnings"] += 1
                _apply_positive_signal(sig, learning, counts)
                sig.aggregated = True
                counts["processed"] += 1
                touched.add(category)

            # Single commit per batch — all the Learnings + signal
            # relinks go out in one write lock acquisition instead of
            # dozens.
            db.session.commit()

            # Global-promote check runs *after* commit so (a) the FK
            # writes are in the DB so the count query sees truth, and
            # (b) the check's SELECT doesn't trigger an autoflush
            # under the write lock. Only look at learnings touched
            # this batch — the drift-dedupe pass handles older ones.
            touched_learnings = set(batch_new_learnings)
            for sid in placed:
                sig = batch_by_id[sid]
                if sig.learning is not None:
                    touched_learnings.add(sig.learning)
            for learning in touched_learnings:
                # Refresh the existing_by_id so later batches in this
                # same category can reuse newly-created Learnings
                # instead of creating parallel duplicates.
                if learning.id is not None:
                    existing_by_id.setdefault(learning.id, learning)
                if _maybe_promote_global(learning):
                    counts["promoted_global"] = counts.get("promoted_global", 0) + 1
            # The global flip is a tiny UPDATE on learnings; commit it
            # on its own so the next batch starts clean.
            db.session.commit()

    counts["touched_categories"] = sorted(touched)
    return counts
