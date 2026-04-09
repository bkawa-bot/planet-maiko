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


def classify_pattern_learnings(batch_size=20):
    """Find Learnings with category='pattern' and reclassify via LLM.

    Updates the learning's rule text (cleaned) and category. Use this
    to clean up the backlog of unsynthesized learnings that accumulated
    from earlier backfills.

    Returns: count of reclassified learnings
    """
    from planet_maiko.models.learning import Learning

    learnings = Learning.query.filter(
        Learning.category == "pattern",
        Learning.status != "dismissed",
    ).limit(batch_size).all()

    if not learnings:
        return 0

    try:
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime()
        if not runtime or not runtime.is_available():
            return 0

        items = []
        for i, l in enumerate(learnings):
            items.append(f"{i+1}. [{l.scope_repo or 'global'}] {l.rule[:300]}")

        prompt = (
            "Synthesize these PR review observations into clean, actionable coding rules.\n"
            "For each observation, extract the core lesson as a short rule (one sentence)\n"
            "and classify it into a category.\n\n"
            "Categories: null_safety, error_handling, testing, performance, "
            "api_design, architecture, security, style, naming, docs, "
            "domain_knowledge, pattern, gotcha\n\n"
            "Observations:\n" + "\n".join(items) + "\n\n"
            "Respond as JSON: {\"rules\": ["
            "{\"index\": 1, \"rule\": \"...\", \"category\": \"...\"}"
            ", ...]}"
        )

        from planet_maiko.agents.routing import resolve_model
        result = runtime.send_json(prompt, timeout=90, model=resolve_model("classify"))

        parsed = result.get("parsed") if isinstance(result, dict) else None
        if not parsed or "rules" not in parsed:
            logger.warning(f"[classifier] No rules in response: {result.get('error') if isinstance(result, dict) else result}")
            return 0

        reclassified = 0
        for rule_data in parsed["rules"]:
            idx = rule_data.get("index", 0) - 1
            if 0 <= idx < len(learnings):
                cat = str(rule_data.get("category", "")).strip().lower()
                rule_text = rule_data.get("rule", "").strip()
                if cat in VALID_CATEGORIES and cat != "pattern" and rule_text:
                    learnings[idx].category = cat
                    learnings[idx].rule = rule_text
                    reclassified += 1

        db.session.commit()
        logger.info(f"[classifier] Reclassified {reclassified}/{len(learnings)} pattern learnings")
        return reclassified

    except Exception as e:
        logger.warning(f"[classifier] Pattern learning reclassification failed: {e}")
        return 0


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

        from planet_maiko.agents.routing import resolve_model
        result = runtime.send_json(prompt, timeout=30, model=resolve_model("classify"))

        # send_json returns {success, output, parsed} — the actual JSON is in parsed
        parsed = result.get("parsed") if isinstance(result, dict) else None
        if not parsed or "classifications" not in parsed:
            logger.warning(f"[classifier] No classifications in response: {result.get('error') if isinstance(result, dict) else result}")
            return 0

        classifications = parsed["classifications"]
        classified = 0
        for i, signal in enumerate(signals):
            if i < len(classifications):
                cat = str(classifications[i]).strip().lower()
                if cat in VALID_CATEGORIES:
                    signal.category = cat
                    classified += 1

        db.session.commit()
        logger.info(f"[classifier] Classified {classified}/{len(signals)} signals")
        return classified

    except Exception as e:
        logger.warning(f"[classifier] Batch classification failed: {e}")

    return 0
