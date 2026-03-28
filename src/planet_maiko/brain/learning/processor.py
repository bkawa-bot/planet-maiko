"""Learning processor - aggregates signals into learnings and graduates them.

Pipeline: raw signals → aggregation → graduation → active rules

Graduation thresholds vary by category:
    - Style/naming/docs: 5 signals (low-stakes, need more evidence)
    - Error handling/null safety/performance/testing: 3 signals
    - API design/architecture/security: 2 signals (high-stakes, graduate faster
      but start as "pending" for user approval)
"""

import logging
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.signal import Signal
from planet_maiko.models.learning import Learning

logger = logging.getLogger(__name__)

# How many signals before a learning graduates
GRADUATION_THRESHOLDS = {
    "style": 5,
    "naming": 5,
    "docs": 5,
    "error_handling": 3,
    "null_safety": 3,
    "performance": 3,
    "testing": 3,
    "api_design": 2,
    "architecture": 2,
    "security": 2,
    "domain_knowledge": 2,
    "pattern": 3,
    "gotcha": 2,
    "team": 3,
}

# Categories that need user approval before becoming active
NEEDS_APPROVAL = {"api_design", "architecture", "security"}

# Confidence increment per signal (capped at 1.0)
CONFIDENCE_PER_SIGNAL = 0.1


def _make_aggregation_key(signal):
    """Build a key for grouping similar signals."""
    # Normalize: first 80 chars of text, lowered
    text_prefix = signal.text[:80].lower().strip()
    parts = [
        signal.category,
        signal.repo or "_global",
        signal.language or "_any",
        text_prefix,
    ]
    return ":".join(parts)


def process_signals():
    """Aggregate unaggregated signals into learnings.

    Returns:
        dict with counts: {processed, new_learnings, updated_learnings, graduated}
    """
    unprocessed = Signal.query.filter_by(aggregated=False).all()

    if not unprocessed:
        return {"processed": 0, "new_learnings": 0, "updated_learnings": 0, "graduated": 0}

    logger.info(f"[learning] Processing {len(unprocessed)} signal(s)...")

    counts = {"processed": 0, "new_learnings": 0, "updated_learnings": 0, "graduated": 0}

    for signal in unprocessed:
        agg_key = _make_aggregation_key(signal)

        # Find existing learning with this aggregation key
        learning = Learning.query.filter_by(aggregation_key=agg_key).first()

        if learning:
            # Update existing learning
            learning.signal_count += 1
            learning.confidence = min(1.0, learning.confidence + CONFIDENCE_PER_SIGNAL)
            learning.last_signal_at = datetime.now(timezone.utc)
            signal.learning_id = learning.id
            counts["updated_learnings"] += 1

            # Check graduation
            threshold = GRADUATION_THRESHOLDS.get(learning.category, 3)
            if learning.signal_count >= threshold and learning.status == "pending":
                if learning.category in NEEDS_APPROVAL:
                    logger.info(f"[learning] Ready for approval: {learning.rule[:60]}")
                else:
                    learning.status = "active"
                    counts["graduated"] += 1
                    logger.info(f"[learning] Graduated: {learning.rule[:60]}")
        else:
            # Create new learning
            learning = Learning(
                rule=signal.text,
                category=signal.category,
                scope_repo=signal.repo,
                scope_language=signal.language,
                confidence=CONFIDENCE_PER_SIGNAL,
                signal_count=1,
                source="auto",
                status="pending",
                aggregation_key=agg_key,
                last_signal_at=datetime.now(timezone.utc),
            )
            db.session.add(learning)
            db.session.flush()  # Get the ID
            signal.learning_id = learning.id
            counts["new_learnings"] += 1

        signal.aggregated = True
        counts["processed"] += 1

    db.session.commit()
    logger.info(f"[learning] Done: {counts}")
    return counts


def _get_learning_success_rates():
    """Compute success rate for each learning based on context selection history.

    Returns:
        dict: learning_id → {"success_rate": float, "total": int}
    """
    from planet_maiko.models.context_selection import ContextSelection

    selections = ContextSelection.query.filter(
        ContextSelection.outcome.isnot(None)
    ).all()

    # Count successes and totals per learning
    stats = {}  # learning_id → {"successes": int, "total": int}
    for sel in selections:
        is_success = sel.outcome == "success"
        for lid in (sel.learning_ids or []):
            if lid not in stats:
                stats[lid] = {"successes": 0, "total": 0}
            stats[lid]["total"] += 1
            if is_success:
                stats[lid]["successes"] += 1

    rates = {}
    for lid, s in stats.items():
        rates[lid] = {
            "success_rate": s["successes"] / s["total"] if s["total"] > 0 else 0.5,
            "total": s["total"],
        }
    return rates


def compile_brief(repo=None, language=None, task_id=None, agent_profile_id=None, max_learnings=15):
    """Compile active learnings into a markdown brief for agents.

    Selects the top learnings by success rate (from context selection history),
    scoped to the relevant repo/language. Records the selection for tracking.

    Args:
        repo: scope to learnings for this repo (plus globals)
        language: scope to learnings for this language (plus globals)
        task_id: if provided, records which learnings were selected (for tracking)
        agent_profile_id: if provided, associates the selection with an agent
        max_learnings: maximum learnings to include in the brief

    Returns:
        str: markdown brief
    """
    query = Learning.query.filter_by(status="active")
    learnings = query.order_by(Learning.confidence.desc()).all()

    # Filter by scope
    scoped = []
    for l in learnings:
        repo_match = l.scope_repo is None or l.scope_repo == repo
        lang_match = l.scope_language is None or l.scope_language == language
        if repo_match and lang_match:
            scoped.append(l)

    if not scoped:
        return "No active learnings yet."

    # Score and rank by success rate (if we have history)
    success_rates = _get_learning_success_rates()

    def sort_key(l):
        rate_info = success_rates.get(l.id)
        if rate_info and rate_info["total"] >= 2:
            # Blend success rate with confidence
            return -(rate_info["success_rate"] * 0.6 + l.confidence * 0.4)
        # No history: fall back to confidence
        return -l.confidence

    scoped.sort(key=sort_key)

    # Take top N
    selected = scoped[:max_learnings]

    # Record selection for tracking
    if task_id:
        from planet_maiko.models.context_selection import ContextSelection
        record = ContextSelection(
            task_id=task_id,
            agent_profile_id=agent_profile_id,
            repo=repo,
            learning_ids=[l.id for l in selected],
            learning_count=len(selected),
        )
        db.session.add(record)
        db.session.commit()

    # Group by category
    by_category = {}
    for l in selected:
        by_category.setdefault(l.category, []).append(l)

    lines = ["# Coding Guidelines (Learned)\n"]
    for category, rules in sorted(by_category.items()):
        lines.append(f"\n## {category.replace('_', ' ').title()}\n")
        for r in rules:
            rate_info = success_rates.get(r.id)
            if rate_info and rate_info["total"] >= 2:
                rate_pct = f"{rate_info['success_rate']*100:.0f}%"
                lines.append(f"- [{rate_pct}] {r.rule}")
            else:
                confidence_bar = "+" * min(5, int(r.confidence * 5))
                lines.append(f"- [{confidence_bar}] {r.rule}")

    return "\n".join(lines)
