"""LoRA training, evaluation, and feedback CLI commands.

Originally a 1100+ line single file. Split into four family modules:

    .training — train, retrain, extract-training-data, generate-rules,
                generate-synthetic
    .review   — eval, eval-prs, review, review-rag
    .feedback — lora-feedback, lora-miss, dedup, add-rule
    .rules    — rules-regen, rule-show, rules-list, rules-relevant

Re-exports every cmd_* by name so existing
`from planet_maiko.cli.lora_cmds import cmd_train` calls continue
to work without touching every import site.
"""

# Re-exports — keep existing imports of `from .lora_cmds import cmd_X`
# resolving without churn.
from .training import (  # noqa: F401
    cmd_train,
    cmd_extract_training_data,
    cmd_generate_synthetic,
    cmd_generate_rules,
    cmd_retrain,
)
from .review import (  # noqa: F401
    cmd_eval_prs,
    cmd_eval,
    cmd_review_rag,
    cmd_review,
)
from .feedback import (  # noqa: F401
    cmd_lora_feedback,
    cmd_lora_miss,
    cmd_dedup,
    cmd_add_rule,
)
from .rules import (  # noqa: F401
    cmd_rules_regen,
    cmd_rule_show,
    cmd_rules_list,
    cmd_rules_relevant,
)


def register(subparsers):
    """Register LoRA training/eval/feedback subcommands."""
    # maiko train
    p = subparsers.add_parser("train", help="Train a LoRA adapter for a repo")
    p.add_argument("repo", nargs="?", help="Repo name like org/repo (omit for --check or --all)")
    p.add_argument("--check", action="store_true", help="Check if training is available")
    p.add_argument("--all", action="store_true", help="Train every configured repo")
    p.set_defaults(func=cmd_train)

    # maiko extract-training-data
    p = subparsers.add_parser("extract-training-data", help="Extract training data from PR history")
    p.add_argument("--limit", type=int, default=200, help="Max PRs per repo")
    p.add_argument("--exclude-from", help="Path to a holdout fixture JSON; PRs listed there are skipped.")
    p.set_defaults(func=cmd_extract_training_data)

    # maiko generate-rules
    p = subparsers.add_parser("generate-rules", help="Generate training data from active learnings")
    p.add_argument("--examples", type=int, default=50, help="Examples per rule (default 50)")
    p.set_defaults(func=cmd_generate_rules)

    # maiko generate-synthetic
    p = subparsers.add_parser("generate-synthetic", help="Generate synthetic training data via Opus")
    p.add_argument("--input", help="Input JSONL dataset (uses latest if omitted)")
    p.add_argument("--limit", type=int, help="Max pairs to process")
    p.set_defaults(func=cmd_generate_synthetic)

    # maiko retrain
    p = subparsers.add_parser("retrain", help="Retrain LoRA adapter with feedback loop")
    p.add_argument("repo", nargs="?", help="Repo name (e.g. org/repo)")
    p.add_argument("--repo-path", help="Local repo path for git log resolution")
    p.add_argument("--skip-feedback", action="store_true", help="Skip feedback resolution step")
    p.add_argument("--skip-datagen", action="store_true", help="Skip training data generation")
    p.add_argument("--force", action="store_true", help="Regenerate data for all rules, not just new ones")
    p.add_argument("--examples", type=int, default=50, help="Examples per rule (default 50)")
    p.set_defaults(func=cmd_retrain)

    # maiko eval (pair-level, on training-data holdout split)
    p = subparsers.add_parser("eval", help="Evaluate a LoRA adapter (pair-level precision/recall)")
    p.add_argument("--adapter", help="Adapter path (uses most recent if omitted)")
    p.add_argument("--repo", help="Filter test data to this repo")
    p.add_argument("--holdout", type=float, default=0.2, help="Fraction of data to hold out for testing (default 0.2)")
    p.add_argument("--per-category", action="store_true", help="Show per-category breakdown")
    p.add_argument(
        "--on-training", action="store_true",
        help="Score the training set instead of holdout. Useful only as a "
             "contrast: a big train-F1 vs holdout-F1 gap is the overfit signal.",
    )
    p.set_defaults(func=cmd_eval)

    # maiko eval-prs (PR-level, against a fixture of real PRs)
    p = subparsers.add_parser(
        "eval-prs",
        help="PR-level holdout eval: run adapter on real PRs + score against human reviews",
    )
    p.add_argument(
        "--fixture",
        default="src/planet_maiko/eval/fixtures/pr-review-v1.json",
        help="Fixture JSON listing PR URLs (default: src/planet_maiko/eval/fixtures/pr-review-v1.json)",
    )
    p.add_argument("--adapter", help="Adapter path (uses most recent if omitted)")
    p.add_argument(
        "--compare-baseline", action="store_true",
        help="Also run each PR without the adapter, report recall delta",
    )
    p.add_argument(
        "--match-mode", choices=("file", "judge"), default="file",
        help="file: count flagged-same-file as hit. judge: ask Haiku whether the model's output "
             "actually addresses the human concern (stricter, slower, costs a few cents per run).",
    )
    p.add_argument(
        "--refresh-ground-truth", action="store_true",
        help="Re-fetch PR comments from GitHub (default uses the cached snapshot "
             "alongside the fixture for reproducibility).",
    )
    p.add_argument(
        "--against",
        help="Path to a previous eval-prs JSON report; emits a delta table and per-PR regressions.",
    )
    p.add_argument("--output", help="Markdown report output path (default: data/eval-reports/holdout-<ts>.md)")
    p.set_defaults(func=cmd_eval_prs)

    # maiko review
    p = subparsers.add_parser("review", help="Review code using a trained LoRA adapter")
    p.add_argument("file", nargs="?", help="File to review (reads stdin if omitted)")
    p.add_argument("--pr", help="GitHub PR URL or Org/Repo#123 — reviews each file individually")
    p.add_argument("--repo", help="Repo name to look up adapter via config.lora.models_by_repo (uses most recent if omitted)")
    p.set_defaults(func=cmd_review)

    # maiko lora-feedback
    p = subparsers.add_parser("lora-feedback", help="Report a LoRA false positive (corrective PASS)")
    p.add_argument("--file", "-f", help="File that was incorrectly flagged")
    p.add_argument("--code", "-c", help="Code snippet that was incorrectly flagged")
    p.add_argument("--repo", help="Repo name (e.g. org/repo)")
    p.add_argument("--output", "-o", help="The incorrect model output (for logging)")
    p.set_defaults(func=cmd_lora_feedback)

    # maiko lora-miss
    p = subparsers.add_parser("lora-miss", help="Report a LoRA false negative (model missed a violation)")
    p.add_argument("--violation", "-v", required=True, help="Description of what should have been caught")
    p.add_argument("--file", "-f", help="File containing the diff chunk")
    p.add_argument("--code", "-c", help="Inline diff chunk the model missed")
    p.add_argument("--category", help="Violation category (e.g. testing, security, architecture)")
    p.add_argument("--repo", help="Repo name (e.g. org/repo)")
    p.set_defaults(func=cmd_lora_miss)

    # maiko dedup
    p = subparsers.add_parser("dedup", help="Merge semantically duplicate learnings")
    p.add_argument("--dry-run", action="store_true", help="Not currently supported; kept for compatibility")
    p.set_defaults(func=cmd_dedup)

    # maiko add-rule
    p = subparsers.add_parser("add-rule", help="Manually add a learning rule")
    p.add_argument("rule", help="The rule text")
    p.add_argument("--category", "-c", default="domain_knowledge",
                   help="Category (default: domain_knowledge)")
    p.add_argument("--repo", help="Scope to a specific repo (omit for global)")
    p.add_argument("--language", help="Scope to a specific language")
    p.set_defaults(func=cmd_add_rule)

    # maiko rule-show
    p = subparsers.add_parser(
        "rule-show",
        help="Print full metadata for one Learning (including Claude-generated description)",
    )
    p.add_argument("id", type=int, help="Learning ID")
    p.set_defaults(func=cmd_rule_show)

    # maiko rules-regen
    p = subparsers.add_parser(
        "rules-regen",
        help="Trigger the rule-description backfill (use --force after prompt changes)",
    )
    p.add_argument("--force", action="store_true",
                   help="Regenerate EVERY active rule's description (not just stale ones)")
    p.add_argument("--foreground", action="store_true",
                   help="Run in this CLI process instead of the Flask background thread")
    p.set_defaults(func=cmd_rules_regen)

    # maiko rules-list
    p = subparsers.add_parser(
        "rules-list",
        help="List learnings with their description status (find IDs for rule-show)",
    )
    p.add_argument("--status", choices=("active", "pending", "dismissed"),
                   help="Filter by status")
    p.add_argument("--repo", help="Filter by scope repo")
    p.add_argument("--category", help="Filter by category")
    p.add_argument("--missing-description", action="store_true",
                   help="Only show learnings without a violation_description")
    p.set_defaults(func=cmd_rules_list)

    # maiko review-rag
    p = subparsers.add_parser(
        "review-rag",
        help="Full RAG review: retrieve relevant rules + Claude reviews diff",
    )
    p.add_argument("file", nargs="?",
                   help="File or diff to review (reads stdin if omitted)")
    p.add_argument("--repo", help="Filter retrieved rules to this repo + globals")
    p.add_argument("--k", type=int, default=5, help="Max rules to surface (default 5)")
    p.add_argument("--min-similarity", type=float, default=0.45,
                   help="Cosine threshold below which rules are dropped (default 0.45)")
    p.set_defaults(func=cmd_review_rag)

    # maiko rules-relevant
    p = subparsers.add_parser(
        "rules-relevant",
        help="Show the team's rules most relevant to a diff or query (RAG retrieval)",
    )
    p.add_argument("file", nargs="?",
                   help="File or diff to retrieve against (reads stdin if omitted "
                        "and no --query is supplied)")
    p.add_argument("--query", action="append", default=[],
                   help="Free-text description to retrieve against (skips diff "
                        "decomposition). Repeat to pass multiple queries.")
    p.add_argument("--repo", help="Filter to rules scoped to this repo + globals")
    p.add_argument("--k", type=int, default=5, help="Max rules to return (default 5)")
    p.add_argument("--min-similarity", type=float, default=0.40,
                   help="Cosine threshold below which rules are dropped (default 0.40)")
    p.add_argument("--job-id", "--task-id", dest="task_id",
                   help="Persist retrieval to task.extra.rules_considered. "
                        "Auto-detected from .maiko-env.json when run inside "
                        "an agent worktree, so agents normally don't pass it. "
                        "(--task-id accepted for back-compat.)")
    p.set_defaults(func=cmd_rules_relevant)
