"""Batch classifier for raw feedback signals using LLM."""

import logging
from planet_maiko.database import db
from planet_maiko.models.signal import Signal

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "null_safety", "error_handling", "testing", "performance",
    "api_design", "architecture", "security", "style", "naming",
    "docs", "domain_knowledge", "pattern", "gotcha",
}


def classify_unclassified_signals(batch_size=20):
    """Find signals with category='pattern' (unclassified) and classify them via LLM.

    Returns: count of classified signals
    """
    # Find unclassified signals (category="pattern" is the default/unclassified value)
    signals = Signal.query.filter(
        Signal.category == "pattern",
        Signal.source_type == "pr_comment",
        Signal.aggregated == False,
    ).limit(batch_size).all()

    if not signals:
        return 0

    try:
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime()
        if not runtime or not runtime.is_available():
            return 0

        # Build batch prompt
        items = []
        for i, s in enumerate(signals):
            items.append(f"{i+1}. [{s.repo or 'unknown'}] {s.text[:200]}")

        prompt = (
            "Classify each code review comment into exactly one category.\n\n"
            "Categories: null_safety, error_handling, testing, performance, "
            "api_design, architecture, security, style, naming, docs, "
            "domain_knowledge, pattern, gotcha\n\n"
            "Comments:\n" + "\n".join(items) + "\n\n"
            "Respond in JSON: {\"classifications\": [\"category1\", \"category2\", ...]}\n"
            "Return one category per comment, in order."
        )

        result = runtime.send_json(prompt, timeout=30)

        if result and "classifications" in result:
            classifications = result["classifications"]
            classified = 0
            for i, signal in enumerate(signals):
                if i < len(classifications):
                    cat = classifications[i].strip().lower()
                    if cat in VALID_CATEGORIES:
                        signal.category = cat
                        classified += 1

            db.session.commit()
            logger.info(f"[classifier] Classified {classified}/{len(signals)} signals")
            return classified

    except Exception as e:
        logger.warning(f"[classifier] Batch classification failed: {e}")

    return 0
