"""Evaluation harnesses for trained LoRA adapters.

The existing `brain.learning.lora_eval` module does pair-level holdout
(did the model say "VIOLATION" vs "PASS" on a held-out training pair?).
That's cheap and useful but doesn't answer the question this module
does: given a set of real PRs with real human review comments, does
the trained model flag the same *files* the humans flagged?

That's the metric that actually tells you whether a retrain made the
model more useful, not just better at reproducing training labels.

Usage (CLI):

    maiko eval-prs \\
        --fixture src/planet_maiko/eval/fixtures/pr-review-v1.json \\
        --adapter ~/.maiko/data/models/lora-org--repo-20260418/ \\
        --compare-baseline

See `holdout.py` for the runner and scoring logic; `fixtures/` holds
JSON files listing the PRs under test (URLs only — ground truth is
fetched fresh from GitHub on each run so it always reflects what's
actually on the PR today).
"""
