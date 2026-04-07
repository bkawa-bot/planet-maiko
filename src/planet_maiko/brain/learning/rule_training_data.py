"""Rule-based training data generation.

For each active learning (rule), generates a balanced training dataset:
  1. Real signals — actual code examples from the team's history
  2. Synthetic violations — Claude generates code that breaks the rule
  3. Synthetic passes — Claude generates code that follows the rule

This produces focused, high-quality training data tied to specific rules
the team cares about, rather than noisy PR-scraping.

Usage:
    from planet_maiko.brain.learning.rule_training_data import generate_rule_dataset
    result = generate_rule_dataset(examples_per_rule=50)
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

EXAMPLES_PER_RULE = 50  # 25 violations + 25 passes by default

SYNTH_PROMPT = """You are generating training data for a code compliance model. The model needs to learn this rule:

**Rule:** {rule}
**Category:** {category}
{scope_info}

{real_examples_section}

Generate {num_violations} VIOLATION examples and {num_passes} PASS examples. Each example should be a realistic code snippet (10-40 lines) that a developer might actually write.

VIOLATION examples should subtly break the rule — not cartoonishly bad code, but realistic mistakes.
PASS examples should follow the rule correctly — clean, idiomatic code.

Vary the examples: different function names, different contexts, different languages if applicable. Make them look like real code from real projects.

Respond with ONLY a JSON object:
{{
  "violations": [
    {{"code": "def foo():\\n    ...", "explanation": "brief explanation of what's wrong"}},
    ...
  ],
  "passes": [
    {{"code": "def bar():\\n    ...", "explanation": "brief explanation of why this is correct"}},
    ...
  ]
}}"""


def generate_rule_dataset(examples_per_rule=EXAMPLES_PER_RULE, output_dir=None, rule_ids=None):
    """Generate training data from active learnings.

    Args:
        examples_per_rule: total examples per rule (split ~50/50 violations/passes)
        output_dir: where to save JSONL
        rule_ids: specific learning IDs to process (None = all active)

    Returns:
        dict with {success, pairs, rules_processed, file_path}
    """
    from planet_maiko.paths import data_dir
    from planet_maiko.database import db
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.signal import Signal
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    if output_dir is None:
        output_dir = os.path.join(data_dir(), "training-data")
    os.makedirs(output_dir, exist_ok=True)

    # Get active learnings
    query = Learning.query.filter_by(status="active")
    if rule_ids:
        query = query.filter(Learning.id.in_(rule_ids))
    learnings = query.all()

    if not learnings:
        return {"success": False, "error": "No active learnings found. Run backfill first."}

    logger.info(f"[rule-data] Processing {len(learnings)} active learnings")

    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        return {"success": False, "error": "LLM runtime not available."}

    all_pairs = []
    rules_processed = 0
    errors = 0

    for learning in learnings:
        logger.info(f"[rule-data] Rule #{learning.id}: {learning.rule[:60]}...")

        # Step 1: Pull real signals for this learning
        real_signals = Signal.query.filter_by(learning_id=learning.id).all()
        real_with_code = [s for s in real_signals if s.code_context]

        # Add real signal pairs
        for s in real_with_code:
            context_parts = []
            if s.file_path:
                context_parts.append(f"File: {s.file_path}")
            if s.repo:
                context_parts.append(f"Repo: {s.repo}")
            context_parts.append(f"```\n{s.code_context}\n```")

            all_pairs.append({
                "input": "\n".join(context_parts),
                "output": f"VIOLATION: [{learning.category}] {s.text}",
                "rule_id": learning.id,
                "rule": learning.rule,
                "category": learning.category,
                "repo": s.repo or "",
                "source": "signal",
            })

        # Step 2: Calculate how many synthetic examples we need
        real_violation_count = len(real_with_code)
        num_violations = max(0, (examples_per_rule // 2) - real_violation_count)
        num_passes = examples_per_rule // 2

        if num_violations == 0 and num_passes == 0:
            rules_processed += 1
            continue

        # Step 3: Build prompt with real examples as reference
        real_examples_section = ""
        if real_with_code:
            examples_text = ""
            for s in real_with_code[:3]:  # Show up to 3 real examples
                examples_text += f"\n- Code: {s.code_context[:300]}\n  Feedback: {s.text}\n"
            real_examples_section = f"Here are real examples of this rule being violated in the team's codebase:\n{examples_text}\nUse these as reference for the style and severity of violations."

        scope_info = ""
        if learning.scope_repo:
            scope_info += f"Repo: {learning.scope_repo}\n"
        if learning.scope_language:
            scope_info += f"Language: {learning.scope_language}\n"

        # Release DB before LLM call
        db.session.close()

        prompt = SYNTH_PROMPT.format(
            rule=learning.rule,
            category=learning.category,
            scope_info=scope_info,
            real_examples_section=real_examples_section,
            num_violations=num_violations,
            num_passes=num_passes,
        )

        result = runtime.send_json(prompt, timeout=180, model=resolve_model("synthetic_data"))

        if not result.get("success") or not result.get("parsed"):
            logger.warning(f"[rule-data] Failed for rule #{learning.id}: {result.get('error', 'no output')}")
            errors += 1
            continue

        parsed = result["parsed"]

        # Add synthetic violations
        for v in parsed.get("violations", []):
            code = v.get("code", "")
            explanation = v.get("explanation", "")
            if not code:
                continue
            all_pairs.append({
                "input": f"```\n{code}\n```",
                "output": f"VIOLATION: [{learning.category}] {explanation}",
                "rule_id": learning.id,
                "rule": learning.rule,
                "category": learning.category,
                "source": "synthetic",
            })

        # Add synthetic passes
        for p in parsed.get("passes", []):
            code = p.get("code", "")
            if not code:
                continue
            all_pairs.append({
                "input": f"```\n{code}\n```",
                "output": "PASS",
                "rule_id": learning.id,
                "rule": learning.rule,
                "category": learning.category,
                "source": "synthetic",
            })

        rules_processed += 1
        logger.info(f"[rule-data] Rule #{learning.id}: {len(parsed.get('violations', []))} synthetic violations, {len(parsed.get('passes', []))} synthetic passes + {real_violation_count} real signals")

    if not all_pairs:
        return {"success": False, "error": "No training pairs generated."}

    # Write dataset
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_path = os.path.join(output_dir, f"rules-{timestamp}.jsonl")
    with open(output_path, "w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    violations = sum(1 for p in all_pairs if not p["output"].startswith("PASS"))
    passes = sum(1 for p in all_pairs if p["output"].startswith("PASS"))

    logger.info(f"[rule-data] Wrote {len(all_pairs)} pairs to {output_path}")

    return {
        "success": True,
        "pairs": len(all_pairs),
        "violations": violations,
        "passes": passes,
        "rules_processed": rules_processed,
        "errors": errors,
        "file_path": output_path,
    }
