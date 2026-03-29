#!/usr/bin/env python3
"""Test harness: Does context actually influence agent behavior?

Runs the same coding task with different context configurations
and checks if the output follows the expected pattern.

Usage:
    python tests/test_context_impact.py

Results are saved to tests/results/ as JSON for analysis.
"""

import json
import os
import sys
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime


# === Test Scenarios ===
# Each scenario has a task, a learning rule, and a check function

SCENARIOS = [
    {
        "name": "null_safety",
        "task": "Write a Java method that fetches a user by ID from a database and returns their display name.",
        "learning": "Always use Optional<T> for nullable return types in Java",
        "check_keywords": ["optional", "Optional"],
        "check_description": "Uses Optional<T> for the return type",
    },
    {
        "name": "error_handling",
        "task": "Write a Python function that calls an external REST API to get weather data and returns the temperature.",
        "learning": "Wrap external API calls in try/except with proper error handling and timeouts",
        "check_keywords": ["try", "except", "timeout"],
        "check_description": "Has try/except and timeout handling",
    },
    {
        "name": "testing_style",
        "task": "Write a Python test for a function that validates email addresses.",
        "learning": "Use parameterized tests for input variation coverage instead of separate test methods",
        "check_keywords": ["parametrize", "parameterize", "param", "test_data", "cases"],
        "check_description": "Uses parameterized testing pattern",
    },
    {
        "name": "security",
        "task": "Write a Python logging function that records API request details for debugging.",
        "learning": "Never log request bodies that may contain PII - sanitize or redact sensitive fields",
        "check_keywords": ["redact", "sanitize", "mask", "PII", "sensitive", "****", "[REDACTED]"],
        "check_description": "Sanitizes or mentions PII/sensitive data handling",
    },
    {
        "name": "api_design",
        "task": "Write a Flask endpoint that returns a list of products with pagination.",
        "learning": "Always version public API endpoints with /v1/ prefix",
        "check_keywords": ["/v1/", "/v1", "version"],
        "check_description": "Includes API versioning in the route",
    },
]


def run_with_context(runtime, task, context_brief):
    """Run a task with a specific context brief."""
    if context_brief:
        prompt = f"""You are writing code. Follow these guidelines:

{context_brief}

## Task
{task}

Write ONLY the code. No explanation."""
    else:
        prompt = f"""You are writing code.

## Task
{task}

Write ONLY the code. No explanation."""

    result = runtime.send(prompt, timeout=180)
    return result


def check_output(output, keywords):
    """Check if output contains any of the expected keywords."""
    output_lower = output.lower()
    hits = [kw for kw in keywords if kw.lower() in output_lower]
    return len(hits) > 0, hits


def run_experiment():
    """Run all scenarios with 3 configurations each."""
    runtime = ClaudeCodeRuntime()
    if not runtime.is_available():
        print("ERROR: Claude CLI not available. Cannot run tests.")
        return

    results = []
    total_scenarios = len(SCENARIOS)

    for i, scenario in enumerate(SCENARIOS):
        print(f"\n{'='*60}")
        print(f"Scenario {i+1}/{total_scenarios}: {scenario['name']}")
        print(f"{'='*60}")

        configs = {
            "no_context": None,
            "global_rule": f"- {scenario['learning']}",
            "global_rule_with_history": (
                f"- {scenario['learning']}\n\n"
                f"Note: This rule was learned from 5 PR reviews. "
                f"Reviewers consistently flagged this pattern. "
                f"Success rate: 87% when followed."
            ),
        }

        scenario_results = {
            "name": scenario["name"],
            "task": scenario["task"],
            "learning": scenario["learning"],
            "check_description": scenario["check_description"],
            "configs": {},
        }

        for config_name, brief in configs.items():
            print(f"\n  Running: {config_name}...")
            start = time.time()

            result = run_with_context(runtime, scenario["task"], brief)
            elapsed = time.time() - start

            if result["success"]:
                passed, hits = check_output(result["output"], scenario["check_keywords"])
                print(f"  Result: {'PASS' if passed else 'FAIL'} ({elapsed:.1f}s)")
                if hits:
                    print(f"  Matched keywords: {hits}")

                scenario_results["configs"][config_name] = {
                    "passed": passed,
                    "matched_keywords": hits,
                    "output_length": len(result["output"]),
                    "elapsed_seconds": round(elapsed, 1),
                    "output_preview": result["output"][:300],
                }
            else:
                print(f"  ERROR: {result['error']}")
                scenario_results["configs"][config_name] = {
                    "passed": False,
                    "error": result["error"],
                    "elapsed_seconds": round(elapsed, 1),
                }

        results.append(scenario_results)

    return results


def print_summary(results):
    """Print a summary table of results."""
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}\n")

    print(f"{'Scenario':<20} {'No Context':<15} {'Global Rule':<15} {'Rule+History':<15}")
    print("-" * 65)

    for r in results:
        no_ctx = "PASS" if r["configs"].get("no_context", {}).get("passed") else "FAIL"
        global_r = "PASS" if r["configs"].get("global_rule", {}).get("passed") else "FAIL"
        history = "PASS" if r["configs"].get("global_rule_with_history", {}).get("passed") else "FAIL"
        print(f"{r['name']:<20} {no_ctx:<15} {global_r:<15} {history:<15}")

    print()

    # Analysis
    improvements = 0
    no_change = 0
    history_helps = 0

    for r in results:
        no_ctx = r["configs"].get("no_context", {}).get("passed", False)
        global_r = r["configs"].get("global_rule", {}).get("passed", False)
        history = r["configs"].get("global_rule_with_history", {}).get("passed", False)

        if not no_ctx and global_r:
            improvements += 1
        if global_r == no_ctx:
            no_change += 1
        if not global_r and history:
            history_helps += 1

    print("Analysis:")
    print(f"  Rules improved output: {improvements}/{len(results)} scenarios")
    print(f"  No change from rules:  {no_change}/{len(results)} scenarios")
    print(f"  History helped beyond rules: {history_helps}/{len(results)} scenarios")

    if improvements > len(results) / 2:
        print("\n  → Context matters! Global rules significantly influence output.")
    if history_helps > 0:
        print("  → Agent history provides additional benefit in some cases.")
    else:
        print("  → Agent history doesn't add much beyond global rules (current design is fine).")


def main():
    print("Planet Maiko — Context Impact Test Harness")
    print("Testing whether learning context actually influences agent behavior\n")
    print(f"Running {len(SCENARIOS)} scenarios × 3 configurations = {len(SCENARIOS) * 3} LLM calls")
    print("Estimated cost: ~$0.15-0.30")
    print("Estimated time: ~2-3 minutes\n")

    if sys.stdin.isatty():
        input("Press Enter to start (or Ctrl+C to cancel)...")

    results = run_experiment()

    if results:
        print_summary(results)

        # Save results
        os.makedirs("tests/results", exist_ok=True)
        filename = f"tests/results/context_impact_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nFull results saved to: {filename}")


if __name__ == "__main__":
    main()
