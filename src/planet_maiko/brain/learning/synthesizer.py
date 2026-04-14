"""LLM synthesis of raw signals.

Turns `synthesized=False` Signal rows into clean, categorized rules by
batching them through Claude. The LLM sees the raw PR-comment body and
decides whether it expresses a generalizable rule; if yes, it rewrites
to a one-sentence rule + picks a category; if no, the row is marked
non-actionable and deleted.

Same logic previously lived inline in api/learning_api._run_backfill_job.
Extracted so the brain cycle's self-healing phase can call it with a
small budget per tick, without re-running the whole backfill.
"""

import logging

from planet_maiko.database import db
from planet_maiko.models.signal import Signal

logger = logging.getLogger(__name__)

# Batch size for a single LLM call. Modest enough that the model stays
# precise on each row; Claude skims when batches get large.
BATCH_SIZE = 40


def synthesize_unsynthesized_signals(max_signals=None, batch_size=BATCH_SIZE,
                                     on_progress=None):
    """Batch `synthesized=False` pr_comment signals through Claude.

    Args:
        max_signals: cap total signals processed this call (None = no cap).
            The cycle phase passes a small number to stay snappy; backfill
            passes None to drain the queue.
        batch_size: signals per LLM call. Default BATCH_SIZE.
        on_progress: optional callback (synthesized_count: int) -> None,
            called after each batch commit so the backfill progress bar
            moves in real time.

    Returns:
        dict with:
            found: int — how many unsynthesized signals existed at start
            processed: int — how many we actually ran through the LLM
            synthesized: int — how many successfully got synthesized=True
            dropped_junk: int — non-actionable signals deleted
            error: str or None
    """
    raw = (Signal.query
           .filter_by(source_type="pr_comment", synthesized=False)
           .order_by(Signal.id.asc())
           .all())
    found = len(raw)
    if max_signals is not None and len(raw) > max_signals:
        raw = raw[:max_signals]

    if not raw:
        return {"found": 0, "processed": 0, "synthesized": 0,
                "dropped_junk": 0, "error": None}

    from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
    from planet_maiko.agents.routing import resolve_model

    runtime = ClaudeCodeRuntime()
    model = resolve_model("classify")

    synthesized = 0
    dropped_junk = 0
    error = None

    for start in range(0, len(raw), batch_size):
        batch = raw[start:start + batch_size]
        comments = [
            f"id={s.id} [{s.repo or 'unknown'}] {s.text[:300]}"
            for s in batch
        ]
        prompt = f"""Synthesize these PR review comments into clean, actionable coding rules.

For each comment, decide whether it expresses a generalizable coding rule
a reviewer would want to reuse — "always prefer X over Y", "don't do Z
in this context". If it does, extract the core lesson as a short one-
sentence rule and classify it. If it doesn't — e.g. greetings, praise,
questions, bot comments, personal opinions, PR-specific logistics — set
actionable: false and we'll drop that signal from the pool.

Echo back every id exactly as given. Include one entry per input.

Comments:
{chr(10).join(comments)}

Categories: security, error_handling, testing, performance, api_design,
architecture, null_safety, style, naming, docs, pattern, domain_knowledge

Respond as JSON:
{{"rules": [
  {{"id": 1234, "actionable": true,
    "rule": "Always validate input lengths at API boundaries",
    "category": "security"}},
  {{"id": 1235, "actionable": false,
    "reason": "Praise without a pattern"}},
  ...
]}}"""

        db.session.close()
        try:
            result = runtime.send_json(prompt, timeout=120, model=model)
        except Exception as e:
            error = str(e)[:300]
            logger.warning(f"[synthesizer] LLM call failed: {error}")
            break

        parsed_rules = (result.get("parsed") or {}).get("rules") if result else None
        if not parsed_rules:
            # Skip this batch — signals stay synthesized=False, next
            # cycle tick retries. Don't commit partial progress.
            logger.info(f"[synthesizer] Batch {start}-{start + len(batch)} had no parseable rules; deferring")
            continue

        # LLMs occasionally return ids as strings ("1234") instead of
        # ints, which used to silently drop the whole row. Coerce
        # numeric strings and log anything we actually have to skip.
        returned_ids = []
        skipped_ids = 0
        for r in parsed_rules:
            if not isinstance(r, dict):
                continue
            rid = r.get("id")
            if isinstance(rid, int):
                returned_ids.append(rid)
                continue
            if isinstance(rid, str) and rid.strip().isdigit():
                coerced = int(rid.strip())
                r["id"] = coerced  # normalize so the lookup loop matches
                returned_ids.append(coerced)
                continue
            skipped_ids += 1
        if skipped_ids:
            logger.debug(f"[synthesizer] Dropped {skipped_ids} rule(s) with non-numeric id in batch {start}-{start + len(batch)}")
        refetched = Signal.query.filter(Signal.id.in_(returned_ids)).all() if returned_ids else []
        by_id = {s.id: s for s in refetched}
        for rule_data in parsed_rules:
            target = by_id.get(rule_data.get("id"))
            if target is None:
                continue
            # Treat missing "actionable" as true so we don't silently
            # drop rules when the LLM forgets the field.
            actionable = rule_data.get("actionable", True)
            if not actionable:
                db.session.delete(target)
                dropped_junk += 1
                continue
            target.text = rule_data.get("rule", target.text)
            target.category = rule_data.get("category", "pattern")
            target.synthesized = True
            synthesized += 1
        db.session.commit()

        if on_progress:
            try:
                on_progress(synthesized)
            except Exception:
                pass

    if dropped_junk:
        logger.info(f"[synthesizer] Dropped {dropped_junk} non-actionable signal(s)")
    if synthesized:
        logger.info(f"[synthesizer] Synthesized {synthesized} signal(s) out of {len(raw)} processed")

    return {
        "found": found,
        "processed": len(raw),
        "synthesized": synthesized,
        "dropped_junk": dropped_junk,
        "error": error,
    }
