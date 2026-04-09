#!/usr/bin/env python3
"""Generate synthetic training data from active learnings.

Calls the ClaudeCodeRuntime (claude CLI) directly for each batch of rules,
producing pass/fail Java code examples for LoRA training.

Run from project root:
    .venv2/bin/python scripts/generate_training_data.py

Progress is saved after each batch — safe to interrupt and resume.
"""

import json
import os
import sys
import time

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from planet_maiko.app import create_app

app = create_app()

RULES_PER_BATCH = 5
EXAMPLES_PER_RULE = 4  # 2 violations + 2 passes


def main():
    with app.app_context():
        from planet_maiko.models.learning import Learning
        from planet_maiko.agents.brain_session import _get_runtime
        from planet_maiko.agents.routing import resolve_model
        from planet_maiko.paths import data_dir

        output_dir = os.path.join(data_dir(), "training-data")
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, "mp-synthetic.jsonl")

        # Load all active rules
        rules = Learning.query.filter_by(status="active").order_by(Learning.id).all()
        print(f"\n  {len(rules)} active rules")

        # Figure out which rules already have synthetic data (for resume)
        done_ids = set()
        if os.path.exists(out_path):
            with open(out_path) as f:
                for line in f:
                    try:
                        done_ids.add(json.loads(line).get("rule_id"))
                    except Exception:
                        pass
            print(f"  {len(done_ids)} rules already done (resuming)")

        remaining = [r for r in rules if r.id not in done_ids]
        if not remaining:
            print("  All rules processed!")
            _print_stats(out_path)
            return

        print(f"  {len(remaining)} rules to process")
        print(f"  {RULES_PER_BATCH} rules/batch, {EXAMPLES_PER_RULE} examples/rule")
        print(f"  Output: {out_path}\n")

        runtime = _get_runtime()
        if not runtime or not runtime.is_available():
            print("  ERROR: LLM runtime not available")
            return

        model = resolve_model("synthetic_data")
        total_pairs = 0
        errors = 0
        start = time.time()

        for batch_start in range(0, len(remaining), RULES_PER_BATCH):
            batch = remaining[batch_start:batch_start + RULES_PER_BATCH]
            batch_num = batch_start // RULES_PER_BATCH + 1
            total_batches = (len(remaining) + RULES_PER_BATCH - 1) // RULES_PER_BATCH

            rules_text = "\n".join(
                f"Rule {i+1} [{r.category}]: {r.rule}"
                for i, r in enumerate(batch)
            )

            prompt = (
                f"Generate training data for a code compliance model. "
                f"For EACH rule below, generate 2 VIOLATION examples and 2 PASS examples. "
                f"Each example: a realistic Java code snippet (10-30 lines) from a "
                f"HubSpot marketplace service using Guice, Immutables, CHIRP, Caffeine cache.\n\n"
                f"{rules_text}\n\n"
                f"Respond as JSON:\n"
                f'{{"rules": [\n'
                f'  {{"rule_index": 1, "violations": [{{"code": "...", "explanation": "..."}}], '
                f'"passes": [{{"code": "...", "explanation": "..."}}]}}\n'
                f"]}}"
            )

            result = runtime.send_json(prompt, timeout=180, model=model)

            batch_pairs = 0
            if result.get("success") and result.get("parsed"):
                parsed = result["parsed"]
                rule_data = parsed if isinstance(parsed, list) else parsed.get("rules", [])

                with open(out_path, "a") as f:
                    for rd in rule_data:
                        idx = rd.get("rule_index", 0) - 1
                        if 0 <= idx < len(batch):
                            r = batch[idx]
                            for v in rd.get("violations", []):
                                if v.get("code"):
                                    f.write(json.dumps({
                                        "input": f"```\n{v['code']}\n```",
                                        "output": f"VIOLATION: [{r.category}] {v.get('explanation', '')}",
                                        "rule_id": r.id,
                                        "rule": r.rule,
                                        "category": r.category,
                                        "source": "synthetic",
                                    }, ensure_ascii=False) + "\n")
                                    batch_pairs += 1
                            for p in rd.get("passes", []):
                                if p.get("code"):
                                    f.write(json.dumps({
                                        "input": f"```\n{p['code']}\n```",
                                        "output": "PASS",
                                        "rule_id": r.id,
                                        "rule": r.rule,
                                        "category": r.category,
                                        "source": "synthetic",
                                    }, ensure_ascii=False) + "\n")
                                    batch_pairs += 1
            else:
                errors += 1

            total_pairs += batch_pairs
            elapsed = time.time() - start
            eta = (elapsed / (batch_start + len(batch))) * (len(remaining) - batch_start - len(batch))
            print(
                f"  [{batch_num}/{total_batches}] +{batch_pairs} pairs "
                f"(total: {total_pairs}, errors: {errors}) "
                f"[{elapsed:.0f}s elapsed, ~{eta:.0f}s remaining]"
            )

        print(f"\n  Done! {total_pairs} new pairs, {errors} errors")
        _print_stats(out_path)


def _print_stats(path):
    if not os.path.exists(path):
        return
    violations = 0
    passes = 0
    with open(path) as f:
        for line in f:
            try:
                p = json.loads(line)
                if p["output"].startswith("PASS"):
                    passes += 1
                else:
                    violations += 1
            except Exception:
                pass
    total = violations + passes
    print(f"\n  Dataset: {total} pairs ({violations} violations, {passes} passes)")
    print(f"  Size: {os.path.getsize(path) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
