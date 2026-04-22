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
                                     on_progress=None, max_workers=3):
    """Batch `synthesized=False` pr_comment signals through Claude.

    Args:
        max_signals: cap total signals processed this call (None = no cap).
            The cycle phase passes a small number to stay snappy; backfill
            passes None to drain the queue.
        batch_size: signals per LLM call. Default BATCH_SIZE.
        on_progress: optional callback (synthesized_count: int) -> None,
            called after each batch commit so the backfill progress bar
            moves in real time.
        max_workers: concurrent LLM calls. Default 3.

    Returns:
        dict with:
            found: int — how many unsynthesized signals existed at start
            processed: int — how many we actually ran through the LLM
            synthesized: int — how many successfully got synthesized=True
            dropped_junk: int — non-actionable signals deleted
            error: str or None
    """
    from planet_maiko.brain.learning.llm_pool import run_parallel

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

    # --- Phase 1: build batch jobs on the main thread. Workers receive
    # only primitives — no ORM instances cross the thread boundary. ---
    jobs = []
    for start in range(0, len(raw), batch_size):
        batch = raw[start:start + batch_size]
        comments = [
            f"id={s.id} [{s.repo or 'unknown'}] {s.text[:300]}"
            for s in batch
        ]
        prompt = f"""Synthesize these PR review comments into clean, actionable coding rules for an autonomous coding agent.

The agent can read code, write code, run tests, and check patterns. It
CANNOT talk to teammates, consult product managers, weigh business
trade-offs, or make judgment calls that require human context.

For each comment, decide:

Mark actionable: true ONLY if the rule is something the agent can
verify or apply by reading or writing code on its own. Good examples:
  - "Always validate input lengths at API boundaries"
  - "Prefer connection pooling over new connections in batch jobs"
  - "Don't swallow exceptions without logging them"
If actionable, extract the core lesson as a short one-sentence rule
and classify it.

Mark actionable: false for anything requiring human judgment, team
coordination, or external decision-making. Examples to drop:
  - "Confirm with the platform team before changing this"
  - "Verify the product use case is worth this complexity"
  - "Check with @alice whether we need this feature"
  - "Make sure leadership signed off on the migration"
  - Greetings, praise, questions, bot comments, PR-specific logistics
  - Rules referencing specific people, tickets, or ongoing discussions

Rule of thumb: if the rule has words like "confirm with", "check with",
"verify with the team", "consult", "get approval", "make sure X agrees",
it's NOT actionable for a coding agent — mark it false.

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
    "reason": "Requires confirming with the platform team"}},
  ...
]}}"""

        jobs.append({
            "start": start,
            "end": start + len(batch),
            "signal_ids": [s.id for s in batch],
            "prompt": prompt,
        })

    # Release the main-thread session — workers don't need it, and
    # on_result will open a fresh session per batch to re-fetch and
    # commit updates.
    db.session.close()

    state = {
        "synthesized": 0,
        "dropped_junk": 0,
        "error": None,
    }

    def runner(job):
        # Worker thread: pure LLM call.
        return runtime.send_json(job["prompt"], timeout=120, model=model)

    def on_result(job, result, error):
        # Main thread: apply DB updates for this batch.
        if error:
            # First hard error wins; subsequent ones get logged but
            # don't overwrite. Workers in flight still complete —
            # ThreadPoolExecutor can't cancel running futures — but
            # their DB writes go through, so any batch that did succeed
            # still lands.
            if state["error"] is None:
                state["error"] = error
            logger.warning(
                f"[synthesizer] Batch {job['start']}-{job['end']} LLM call failed: {error}"
            )
            return

        parsed_rules = (result.get("parsed") or {}).get("rules") if result else None
        if not parsed_rules:
            logger.info(
                f"[synthesizer] Batch {job['start']}-{job['end']} "
                "had no parseable rules; deferring"
            )
            return

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
                r["id"] = coerced
                returned_ids.append(coerced)
                continue
            skipped_ids += 1
        if skipped_ids:
            logger.debug(
                f"[synthesizer] Dropped {skipped_ids} rule(s) with "
                f"non-numeric id in batch {job['start']}-{job['end']}"
            )

        # Re-fetch signals fresh — the ones loaded in Phase 1 are
        # detached after db.session.close(). Filter to ids we actually
        # asked about so we don't pick up stray rows with the same id.
        allowed = set(job["signal_ids"])
        lookup_ids = [rid for rid in returned_ids if rid in allowed]
        refetched = (Signal.query.filter(Signal.id.in_(lookup_ids)).all()
                     if lookup_ids else [])
        by_id = {s.id: s for s in refetched}
        batch_synthesized = 0
        for rule_data in parsed_rules:
            target = by_id.get(rule_data.get("id"))
            if target is None:
                continue
            # Missing "actionable" defaults to true so we don't silently
            # drop rules when the LLM forgets the field.
            actionable = rule_data.get("actionable", True)
            if not actionable:
                db.session.delete(target)
                state["dropped_junk"] += 1
                continue
            # Preserve the raw comment body before synthesis rewrites
            # it. The provenance UI shows original_text so the user can
            # see exactly what a reviewer actually said, not the LLM
            # paraphrase. First-time set only — if original_text is
            # already populated, a previous synthesis pass stashed it.
            if not target.original_text:
                target.original_text = target.text
            target.text = rule_data.get("rule", target.text)
            target.category = rule_data.get("category", "pattern")
            target.synthesized = True
            batch_synthesized += 1
        db.session.commit()
        state["synthesized"] += batch_synthesized

        if on_progress:
            try:
                on_progress(state["synthesized"])
            except Exception:
                pass

    run_parallel(jobs, runner, max_workers=max_workers,
                 on_result=on_result, log_prefix="synth")

    if state["dropped_junk"]:
        logger.info(f"[synthesizer] Dropped {state['dropped_junk']} non-actionable signal(s)")
    if state["synthesized"]:
        logger.info(f"[synthesizer] Synthesized {state['synthesized']} signal(s) out of {len(raw)} processed")

    return {
        "found": found,
        "processed": len(raw),
        "synthesized": state["synthesized"],
        "dropped_junk": state["dropped_junk"],
        "error": state["error"],
    }
