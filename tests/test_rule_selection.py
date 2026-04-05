#!/usr/bin/env python3
"""Test harness: Does SELECTING the right rules matter?

Tests whether smart rule selection outperforms:
- Sending ALL rules (kitchen sink)
- Sending RANDOM rules (same count, wrong ones)
- Sending NO rules (baseline)

Usage:
    python tests/test_rule_selection.py
"""

import json
import os
import random
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime


# === 50 Realistic Coding Rules (mixed relevance) ===

ALL_RULES = [
    # Null safety (relevant for Java tasks)
    "Always use Optional<T> for nullable return types in Java",
    "Check for null before accessing nested object properties",
    "Use @Nullable annotation on method parameters that accept null",

    # Error handling (relevant for API tasks)
    "Wrap external API calls in try/except with proper error handling",
    "Always set timeouts on HTTP requests (default: 30s)",
    "Log the full stack trace when catching unexpected exceptions",
    "Use circuit breaker pattern for unreliable external services",
    "Return meaningful error messages in API responses, not stack traces",

    # Testing (relevant for test tasks)
    "Use parameterized tests for input variation coverage",
    "Mock external dependencies in unit tests",
    "Test edge cases: empty input, null, boundary values",
    "Aim for at least 80% code coverage on critical paths",
    "Use fixtures for test data setup, not inline construction",

    # Security (relevant for auth/API tasks)
    "Never log request bodies that may contain PII",
    "Validate and sanitize all user input before processing",
    "Use parameterized queries to prevent SQL injection",
    "Store passwords using bcrypt, never plain text",
    "Rate limit authentication endpoints",

    # API design (relevant for endpoint tasks)
    "Always version public API endpoints with /v1/ prefix",
    "Use consistent naming: camelCase for JSON, snake_case for Python",
    "Return appropriate HTTP status codes (201 for created, 404 for not found)",
    "Include pagination for list endpoints (limit/offset or cursor)",
    "Document API endpoints with OpenAPI/Swagger annotations",

    # Performance (relevant for data tasks)
    "Use connection pooling for all database access",
    "Add database indexes on columns used in WHERE clauses",
    "Use batch inserts for bulk operations (>10 rows)",
    "Cache frequently accessed data with TTL-based expiration",
    "Use async/await for I/O-bound operations",

    # Code style (generally low relevance)
    "Keep functions under 30 lines",
    "Use descriptive variable names, not single letters",
    "Add docstrings to all public functions",
    "Use type hints in Python function signatures",
    "Prefer composition over inheritance",

    # Database (relevant for DB tasks)
    "Always use transactions for multi-step database operations",
    "Use database migrations for schema changes, never raw ALTER TABLE",
    "Add created_at and updated_at timestamps to all tables",
    "Use UUIDs for primary keys in distributed systems",
    "Implement soft deletes with a deleted_at column",

    # Frontend (irrelevant for backend tasks)
    "Use semantic HTML elements (nav, main, article)",
    "Add aria-labels to interactive elements for accessibility",
    "Lazy load images below the fold",
    "Use CSS custom properties for theming",
    "Debounce search input handlers (300ms)",

    # DevOps (irrelevant for code tasks)
    "Pin dependency versions in requirements.txt",
    "Use multi-stage Docker builds to reduce image size",
    "Set resource limits on Kubernetes pods",
    "Use structured logging (JSON format) for log aggregation",
    "Add health check endpoints for load balancer probes",
]

# === Test Scenarios ===

SCENARIOS = [
    {
        "name": "java_api_endpoint",
        "task": "Write a Java Spring Boot REST controller method that fetches a user by ID from a UserRepository and returns their profile. The user might not exist.",
        "relevant_rule_indices": [0, 1, 2, 7, 20],  # null safety + API design
        "check_keywords": ["Optional", "optional", "ResponseEntity", "404", "NotFound"],
        "check_description": "Handles null user with Optional or proper 404",
    },
    {
        "name": "python_external_api",
        "task": "Write a Python function that calls the OpenWeatherMap API to get the current temperature for a city, with proper error handling.",
        "relevant_rule_indices": [3, 4, 5, 6, 7],  # error handling
        "check_keywords": ["try", "except", "timeout", "raise", "error"],
        "check_description": "Has error handling with timeout",
    },
    {
        "name": "database_bulk_insert",
        "task": "Write a Python function that takes a list of user dictionaries and inserts them into a PostgreSQL database using psycopg2.",
        "relevant_rule_indices": [25, 26, 27, 35, 36],  # performance + database
        "check_keywords": ["executemany", "batch", "execute_batch", "VALUES", "transaction", "commit"],
        "check_description": "Uses batch insert pattern",
    },
    {
        "name": "auth_endpoint",
        "task": "Write a Flask login endpoint that accepts username and password, checks against the database, and returns a JWT token.",
        "relevant_rule_indices": [13, 14, 15, 16, 17, 18],  # security
        "check_keywords": ["bcrypt", "hash", "sanitize", "rate", "parameterized", "sql"],
        "check_description": "Addresses security concerns (hashing, injection, etc.)",
    },
]


def build_brief(rule_indices):
    """Build a brief from specific rule indices."""
    rules = [ALL_RULES[i] for i in rule_indices if i < len(ALL_RULES)]
    return "\n".join(f"- {r}" for r in rules)


def run_with_brief(runtime, task, brief):
    """Run a task with a specific brief."""
    if brief:
        prompt = f"""You are writing production code. Follow these coding guidelines:

{brief}

## Task
{task}

Write ONLY the code. No explanation."""
    else:
        prompt = f"""You are writing production code.

## Task
{task}

Write ONLY the code. No explanation."""

    return runtime.send(prompt, timeout=180)


def check_output(output, keywords):
    """Check if output contains any of the expected keywords."""
    output_lower = output.lower()
    hits = [kw for kw in keywords if kw.lower() in output_lower]
    return len(hits) > 0, hits


def run_experiment():
    runtime = ClaudeCodeRuntime()
    if not runtime.is_available():
        print("ERROR: Claude CLI not available.")
        return None

    results = []

    for i, scenario in enumerate(SCENARIOS):
        print(f"\n{'='*60}")
        print(f"Scenario {i+1}/{len(SCENARIOS)}: {scenario['name']}")
        print(f"Check: {scenario['check_description']}")
        print(f"{'='*60}")

        # Pick 5 random rules that are NOT the relevant ones
        irrelevant_indices = [j for j in range(len(ALL_RULES)) if j not in scenario["relevant_rule_indices"]]
        random_indices = random.sample(irrelevant_indices, 5)

        configs = {
            "no_rules": None,
            "5_relevant": build_brief(scenario["relevant_rule_indices"]),
            "5_random": build_brief(random_indices),
            "all_50": build_brief(list(range(len(ALL_RULES)))),
        }

        scenario_results = {
            "name": scenario["name"],
            "task": scenario["task"],
            "check_description": scenario["check_description"],
            "relevant_rules": [ALL_RULES[j] for j in scenario["relevant_rule_indices"]],
            "random_rules": [ALL_RULES[j] for j in random_indices],
            "configs": {},
        }

        for config_name, brief in configs.items():
            rule_count = 0 if brief is None else brief.count("\n") + 1
            print(f"\n  Running: {config_name} ({rule_count} rules)...")
            start = time.time()

            result = run_with_brief(runtime, scenario["task"], brief)
            elapsed = time.time() - start

            if result["success"]:
                passed, hits = check_output(result["output"], scenario["check_keywords"])
                print(f"  Result: {'PASS' if passed else 'FAIL'} ({elapsed:.1f}s, {len(result['output'])} chars)")
                if hits:
                    print(f"  Matched: {hits}")

                scenario_results["configs"][config_name] = {
                    "passed": passed,
                    "matched_keywords": hits,
                    "keyword_count": len(hits),
                    "output_length": len(result["output"]),
                    "elapsed_seconds": round(elapsed, 1),
                    "output_preview": result["output"][:400],
                }
            else:
                print(f"  ERROR: {result['error']}")
                scenario_results["configs"][config_name] = {
                    "passed": False,
                    "error": result["error"],
                }

        results.append(scenario_results)

    return results


def print_summary(results):
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}\n")

    print(f"{'Scenario':<25} {'No Rules':<12} {'5 Relevant':<12} {'5 Random':<12} {'All 50':<12}")
    print("-" * 73)

    for r in results:
        vals = {}
        for config in ["no_rules", "5_relevant", "5_random", "all_50"]:
            c = r["configs"].get(config, {})
            if c.get("error"):
                vals[config] = "ERROR"
            elif c.get("passed"):
                vals[config] = f"PASS({c.get('keyword_count', 0)})"
            else:
                vals[config] = "FAIL"

        print(f"{r['name']:<25} {vals.get('no_rules', '?'):<12} {vals.get('5_relevant', '?'):<12} {vals.get('5_random', '?'):<12} {vals.get('all_50', '?'):<12}")

    print()

    # Analysis
    relevant_wins = 0
    random_matches_relevant = 0
    all50_matches_relevant = 0

    for r in results:
        rel = r["configs"].get("5_relevant", {})
        rand = r["configs"].get("5_random", {})
        all50 = r["configs"].get("all_50", {})

        rel_hits = rel.get("keyword_count", 0)
        rand_hits = rand.get("keyword_count", 0)
        all50_hits = all50.get("keyword_count", 0)

        if rel_hits > rand_hits:
            relevant_wins += 1
        if rand_hits >= rel_hits:
            random_matches_relevant += 1
        if all50_hits >= rel_hits:
            all50_matches_relevant += 1

    total = len(results)
    print("Analysis:")
    print(f"  5 relevant beat 5 random:  {relevant_wins}/{total} scenarios")
    print(f"  5 random matched relevant: {random_matches_relevant}/{total} scenarios")
    print(f"  All 50 matched relevant:   {all50_matches_relevant}/{total} scenarios")

    if relevant_wins > total / 2:
        print("\n  → SELECTION MATTERS! Picking the right rules outperforms random selection.")
        print("  → This justifies building the context optimization / agent specialization system.")
    elif all50_matches_relevant >= total / 2:
        print("\n  → Just sending everything works fine. Smart selection may not be worth the complexity.")
    else:
        print("\n  → Results are mixed. More data needed.")


def main():
    print("Planet Maiko — Rule Selection Test")
    print("Does picking the RIGHT rules matter, or can we just send everything?\n")
    print(f"Running {len(SCENARIOS)} scenarios × 4 configurations = {len(SCENARIOS) * 4} LLM calls")
    print("Estimated cost: ~$0.30-0.50")
    print("Estimated time: ~5-8 minutes\n")

    if sys.stdin.isatty():
        input("Press Enter to start (or Ctrl+C to cancel)...")

    results = run_experiment()

    if results:
        print_summary(results)

        os.makedirs("tests/results", exist_ok=True)
        filename = f"tests/results/rule_selection_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nFull results saved to: {filename}")


if __name__ == "__main__":
    main()
