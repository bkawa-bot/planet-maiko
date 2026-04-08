"""Learning processor - aggregates signals into learnings and graduates them.

Pipeline: raw signals → aggregation → graduation → active rules

Graduation thresholds vary by category:
    - Style/naming/docs: 5 signals (low-stakes, need more evidence)
    - Error handling/null safety/performance/testing: 3 signals
    - API design/architecture/security: 2 signals (high-stakes, graduate faster
      but start as "pending" for user approval)
"""

import logging
import os
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
            # Handle negative signals from LoRA hook feedback
            if signal.source_type == "lora_hook" and signal.severity == "rejected":
                learning.confidence = max(0.0, learning.confidence - CONFIDENCE_PER_SIGNAL)
                learning.last_signal_at = datetime.now(timezone.utc)
                signal.learning_id = learning.id
                counts["updated_learnings"] += 1

                # Auto-dismiss if confidence is very low and rejections dominate
                if learning.confidence < 0.1 and learning.status == "active":
                    # Count accepted vs rejected signals for this learning
                    reject_count = Signal.query.filter_by(
                        learning_id=learning.id, source_type="lora_hook", severity="rejected"
                    ).count()
                    accept_count = Signal.query.filter_by(
                        learning_id=learning.id, source_type="lora_hook", severity="suggestion"
                    ).count()
                    if reject_count > accept_count:
                        learning.status = "dismissed"
                        logger.info(f"[learning] Auto-dismissed (too many rejections): {learning.rule[:60]}")

                signal.aggregated = True
                counts["processed"] += 1
                continue

            # Update existing learning (positive signal)
            learning.signal_count += 1
            learning.confidence = min(1.0, learning.confidence + CONFIDENCE_PER_SIGNAL)
            learning.last_signal_at = datetime.now(timezone.utc)
            signal.learning_id = learning.id
            counts["updated_learnings"] += 1

            # Check graduation (don't graduate unclassified "pattern" signals)
            threshold = GRADUATION_THRESHOLDS.get(learning.category, 3)
            if learning.signal_count >= threshold and learning.status == "pending":
                if learning.category == "pattern" and learning.source == "auto":
                    pass  # Wait for LLM classification before graduating
                elif learning.category in NEEDS_APPROVAL:
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

    # Export coding guidelines if any learnings graduated
    if counts["graduated"] > 0:
        export_coding_guidelines()

    logger.info(f"[learning] Done: {counts}")
    return counts


def export_coding_guidelines(output_path=None):
    """Export active learnings to a coding guidelines markdown file.

    Called after learning graduation to keep the guidelines file in sync.
    """
    if output_path is None:
        from planet_maiko.paths import data_dir
        output_path = os.path.join(data_dir(), "coding-guidelines.md")

    learnings = Learning.query.filter_by(status="active").order_by(Learning.category, Learning.confidence.desc()).all()

    if not learnings:
        return

    lines = ["# Coding Guidelines (Auto-Learned)\n"]
    lines.append(f"*Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n")
    lines.append(f"*{len(learnings)} active rules from team feedback and PR reviews.*\n")

    by_category = {}
    for l in learnings:
        by_category.setdefault(l.category, []).append(l)

    for category, rules in sorted(by_category.items()):
        lines.append(f"\n## {category.replace('_', ' ').title()}\n")
        for r in rules:
            confidence = "+" * min(5, int(r.confidence * 5))
            lines.append(f"- [{confidence}] {r.rule}")
            if r.scope_repo:
                lines.append(f"  *(repo: {r.scope_repo})*")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    logger.info(f"[learning] Exported {len(learnings)} guidelines to {output_path}")


def compile_brief(repo=None, language=None, max_learnings=15, **_kwargs):
    """Compile active learnings into a markdown brief.

    Simple confidence-ranked selection scoped by repo/language.
    LoRA handles rule enforcement via model weights — this brief is
    for reference or non-LoRA agents only.

    Args:
        repo: scope to learnings for this repo (plus globals)
        language: scope to learnings for this language (plus globals)
        max_learnings: maximum learnings to include

    Returns:
        str: markdown brief
    """
    learnings = Learning.query.filter_by(status="active").order_by(
        Learning.confidence.desc()
    ).all()

    # Filter by scope
    scoped = []
    for l in learnings:
        repo_match = l.scope_repo is None or l.scope_repo == repo
        lang_match = l.scope_language is None or l.scope_language == language
        if repo_match and lang_match:
            scoped.append(l)

    if not scoped:
        return "No active learnings yet."

    selected = scoped[:max_learnings]

    by_category = {}
    for l in selected:
        by_category.setdefault(l.category, []).append(l)

    lines = ["# Coding Guidelines (Learned)\n"]
    for category, rules in sorted(by_category.items()):
        lines.append(f"\n## {category.replace('_', ' ').title()}\n")
        for r in rules:
            confidence_bar = "+" * min(5, int(r.confidence * 5))
            lines.append(f"- [{confidence_bar}] {r.rule}")

    return "\n".join(lines)
