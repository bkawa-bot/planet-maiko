"""Learning processor - helpers for signal → learning aggregation.

Historical auto-graduation (per-category thresholds, NEEDS_APPROVAL
categories) has been removed — every Learning now stays in "pending"
status until the user explicitly approves it in the Knowledge UI.
signal_count is just metadata (how many confirming signals), used by
the UI to sort/highlight rules with more evidence.

The main aggregation path now lives in clustering.cluster_signals_into_learnings.
The helpers below (_apply_positive_signal, etc.) are still used by
that module. Junk filtering is handled upstream — the LLM synthesis
step flags non-actionable signals and we delete them before they ever
reach aggregation.
"""

import logging
import os
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.signal import Signal
from planet_maiko.models.learning import Learning

logger = logging.getLogger(__name__)

# Confidence increment per signal (capped at 1.0). Confidence is a
# "how strongly does the evidence back this rule" score the UI shows
# — it doesn't gate graduation.
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


def _apply_negative_signal(signal, learning, counts):
    """LoRA hook said this rule is wrong — decrement confidence and
    auto-dismiss if rejections dominate.
    """
    learning.confidence = max(0.0, learning.confidence - CONFIDENCE_PER_SIGNAL)
    learning.last_signal_at = datetime.now(timezone.utc)
    signal.learning_id = learning.id
    counts["updated_learnings"] += 1

    # Auto-dismiss if confidence cratered and rejections dominate
    if learning.confidence < 0.1 and learning.status == "active":
        reject_count = Signal.query.filter_by(
            learning_id=learning.id, source_type="lora_hook", severity="rejected"
        ).count()
        accept_count = Signal.query.filter_by(
            learning_id=learning.id, source_type="lora_hook", severity="suggestion"
        ).count()
        if reject_count > accept_count:
            learning.status = "dismissed"
            logger.info(f"[learning] Auto-dismissed (too many rejections): {learning.rule[:60]}")


def _apply_positive_signal(signal, learning, counts):
    """A confirming signal — bump signal count + confidence, link the
    signal to the learning. The learning's status stays "pending" — the
    user has to explicitly approve it in the Knowledge UI.
    """
    learning.signal_count += 1
    learning.confidence = min(1.0, learning.confidence + CONFIDENCE_PER_SIGNAL)
    learning.last_signal_at = datetime.now(timezone.utc)
    signal.learning_id = learning.id
    counts["updated_learnings"] += 1


def _create_new_learning(signal, agg_key, counts):
    """No existing learning matched this aggregation key — create one
    in pending state and link the signal to it.
    """
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
    db.session.flush()  # Get the autoincrement id
    signal.learning_id = learning.id
    counts["new_learnings"] += 1


def process_signals():
    """Aggregate unaggregated signals into learnings.

    Returns:
        dict with counts: {processed, new_learnings, updated_learnings,
        graduated}
    """
    unprocessed = Signal.query.filter_by(aggregated=False).all()
    if not unprocessed:
        return {"processed": 0, "new_learnings": 0, "updated_learnings": 0, "graduated": 0}

    logger.info(f"[learning] Processing {len(unprocessed)} signal(s)...")
    counts = {"processed": 0, "new_learnings": 0, "updated_learnings": 0,
              "graduated": 0}

    for signal in unprocessed:
        agg_key = _make_aggregation_key(signal)
        learning = Learning.query.filter_by(aggregation_key=agg_key).first()

        if learning is None:
            _create_new_learning(signal, agg_key, counts)
        elif signal.source_type == "lora_hook" and signal.severity == "rejected":
            _apply_negative_signal(signal, learning, counts)
        else:
            _apply_positive_signal(signal, learning, counts)

        signal.aggregated = True
        counts["processed"] += 1

    db.session.commit()

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


def compile_brief(repo=None, language=None, max_learnings=15, task_id=None,
                  agent_profile_id=None, **_kwargs):
    """Compile active learnings into a markdown brief.

    Simple confidence-ranked selection scoped by repo/language.
    LoRA handles rule enforcement via model weights — this brief is
    for reference or non-LoRA agents only.

    Args:
        repo: scope to learnings for this repo (plus globals)
        language: scope to learnings for this language (plus globals)
        max_learnings: maximum learnings to include
        task_id: if provided, record a ContextSelection linking the
                 selected learnings to this task so record_task_outcome
                 can later attribute success/failure to the chosen context
        agent_profile_id: which agent this brief is for (for outcome stats)

    Returns:
        str: markdown brief
    """
    learnings = Learning.query.filter_by(status="active").order_by(
        Learning.confidence.desc()
    ).all()

    # Filter by scope
    scoped = []
    for l in learnings:
        repo_match = l.is_global or l.scope_repo is None or l.scope_repo == repo
        lang_match = l.scope_language is None or l.scope_language == language
        if repo_match and lang_match:
            scoped.append(l)

    if not scoped:
        return "No active learnings yet."

    selected = scoped[:max_learnings]

    # Record the selection so the outcome (success/failure) can later
    # be attributed back to this specific context. Skipped if task_id
    # not provided (e.g. previewing the brief in the UI).
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
