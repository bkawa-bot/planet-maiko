"""Synthetic training data generation via Claude Opus.

Takes raw diffs extracted from PR history, sends them to Opus in batches
for structured code review, and outputs clean training pairs. Produces
higher-quality labels than raw PR comments.

Usage:
    from planet_maiko.brain.learning.synthetic_data import generate_synthetic_dataset
    result = generate_synthetic_dataset(input_dataset="path/to/raw.jsonl")
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BATCH_SIZE = 5  # Diffs per LLM call

REVIEW_PROMPT = """You are a senior code reviewer producing structured training data. For each code change below, produce a clean review verdict.

Some changes include the original reviewer's comment. When present, use it as your primary signal — rewrite it as a clean, structured verdict. When no reviewer comment is provided, the code merged cleanly — confirm it's clean or flag any issues you see.

For EACH change, respond with:
- "PASS" if the code is clean and follows good practices
- "VIOLATION: [category] specific, actionable description of the issue and how to fix it"

Categories: security, performance, error_handling, testing, style, naming, architecture, bug, null_safety

Rules:
- If a reviewer flagged it, trust them — structure their feedback, don't dismiss it
- Be specific: name the function, variable, or pattern that's wrong
- Be actionable: say what to do instead
- One verdict per change, even if there are multiple issues (pick the most important)

{diffs}

Respond with ONLY a JSON array, one entry per change:
[
  {{"index": 0, "verdict": "PASS"}},
  {{"index": 1, "verdict": "VIOLATION: [security] SQL query built with string concatenation in build_query() — use parameterized queries to prevent injection"}},
  ...
]"""


def generate_synthetic_dataset(input_dataset=None, output_dir=None, limit=None):
    """Generate synthetic training data by running diffs through Opus.

    Args:
        input_dataset: path to raw JSONL (from extract_training_data).
                       If None, uses the most recent raw dataset.
        output_dir: where to save (defaults to data/training-data/)
        limit: max pairs to process (None = all)

    Returns:
        dict with {pairs, file_path, batches, errors}
    """
    from planet_maiko.paths import data_dir
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    if output_dir is None:
        output_dir = os.path.join(data_dir(), "training-data")
    os.makedirs(output_dir, exist_ok=True)

    # Find input dataset. Excludes:
    # - synthetic-*.jsonl: outputs of THIS function, would synth-on-synth
    # - rules-*.jsonl: outputs of generate_rule_dataset; those already
    #   contain synthetic pairs interleaved with real signals, so feeding
    #   them in produces synth-of-synth (degraded variations of generated
    #   examples, not of real comments).
    if not input_dataset:
        candidates = [
            f for f in os.listdir(output_dir)
            if f.endswith(".jsonl")
            and not f.startswith("synthetic-")
            and not f.startswith("rules-")
        ]
        files = sorted(candidates, reverse=True)
        if not files:
            return {
                "success": False,
                "error": (
                    "No raw dataset found. The new rule-based pipeline "
                    "(rules-*.jsonl) already produces synthetic pairs — "
                    "you usually don't need to run generate-synthetic. "
                    "If you specifically want this older flow, "
                    "run `maiko extract-training-data` first to produce "
                    "an input file, then re-run."
                ),
            }
        input_dataset = os.path.join(output_dir, files[0])

    # Load raw pairs, filtering out anything already tagged as synthetic.
    # Even when an explicit --input is passed, an unsuspecting user
    # might point at a rules-*.jsonl; this guard keeps the synth from
    # multiplying its own noise.
    raw_pairs = []
    skipped_synth = 0
    with open(input_dataset) as f:
        for line in f:
            try:
                pair = json.loads(line)
            except json.JSONDecodeError:
                continue
            if pair.get("source") == "synthetic":
                skipped_synth += 1
                continue
            raw_pairs.append(pair)

    if not raw_pairs:
        return {
            "success": False,
            "error": (
                f"Input dataset has no real (non-synthetic) pairs"
                f"{f' (skipped {skipped_synth} synthetic ones)' if skipped_synth else ''}. "
                "Did you point at a rules-*.jsonl by mistake?"
            ),
        }

    if limit:
        raw_pairs = raw_pairs[:limit]

    if skipped_synth:
        logger.info(
            f"[synthetic] Skipped {skipped_synth} pre-existing synthetic pairs from input"
        )
    logger.info(f"[synthetic] Processing {len(raw_pairs)} real pairs from {input_dataset}")

    # Get runtime
    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        return {"success": False, "error": "LLM runtime not available."}

    # Process in batches
    synthetic_pairs = []
    errors = 0
    batches = 0

    for i in range(0, len(raw_pairs), BATCH_SIZE):
        batch = raw_pairs[i:i + BATCH_SIZE]
        batches += 1

        # Build the diffs section with full context
        diffs_text = ""
        for j, pair in enumerate(batch):
            diffs_text += f"\n--- Change {j} ---\n"
            if pair.get("pr_title"):
                diffs_text += f"PR: {pair['pr_title']}\n"
            if pair.get("file_path"):
                diffs_text += f"File: {pair['file_path']}\n"
            diffs_text += f"{pair['input']}\n"
            # Include original reviewer comment if this was a violation
            original = pair.get("output", "")
            if original.startswith("VIOLATION:"):
                diffs_text += f"Original reviewer comment: {original[len('VIOLATION:'):].strip()}\n"

        prompt = REVIEW_PROMPT.format(diffs=diffs_text)

        result = runtime.send_json(prompt, timeout=180, model=resolve_model("synthetic_data"))

        if not result.get("success") or not result.get("parsed"):
            logger.warning(f"[synthetic] Batch {batches} failed: {result.get('error', 'no parsed output')}")
            errors += 1
            # Fall through with raw labels for this batch
            for pair in batch:
                synthetic_pairs.append(pair)
            continue

        # Parse verdicts
        verdicts = result["parsed"]
        if not isinstance(verdicts, list):
            verdicts = verdicts.get("reviews", verdicts.get("results", []))

        for j, pair in enumerate(batch):
            # Find matching verdict
            verdict_text = None
            for v in verdicts:
                if v.get("index") == j:
                    verdict_text = v.get("verdict", "").strip()
                    break

            if not verdict_text:
                # No verdict for this index, keep raw
                synthetic_pairs.append(pair)
                continue

            synthetic_pairs.append({
                "input": pair["input"],
                "output": verdict_text,
                "repo": pair.get("repo", ""),
                "file_path": pair.get("file_path", ""),
                "pr_number": pair.get("pr_number"),
                "pr_title": pair.get("pr_title", ""),
                "source": "synthetic",
            })

        logger.info(f"[synthetic] Batch {batches}/{(len(raw_pairs) + BATCH_SIZE - 1) // BATCH_SIZE} done")

    # Write output
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_path = os.path.join(output_dir, f"synthetic-{timestamp}.jsonl")
    with open(output_path, "w", encoding="utf-8") as f:
        for pair in synthetic_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    violations = sum(1 for p in synthetic_pairs if not p["output"].startswith("PASS"))
    passes = sum(1 for p in synthetic_pairs if p["output"].startswith("PASS"))

    logger.info(f"[synthetic] Wrote {len(synthetic_pairs)} pairs to {output_path}")
    logger.info(f"[synthetic] {violations} violations, {passes} passes, {errors} batch errors")

    return {
        "success": True,
        "pairs": len(synthetic_pairs),
        "violations": violations,
        "passes": passes,
        "batches": batches,
        "errors": errors,
        "file_path": output_path,
    }
