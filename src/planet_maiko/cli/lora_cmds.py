"""LoRA training, evaluation, and feedback CLI commands.

Commands:
- train, retrain — fine-tune adapters from learnings + corrections
- extract-training-data, generate-rules, generate-synthetic — build datasets
- eval — held-out precision/recall/F1 on a trained adapter
- review — review a file or PR through the trained model
- lora-feedback — record a false positive (corrective PASS)
- lora-miss — record a false negative (corrective VIOLATION)
- dedup — merge semantically duplicate learnings (+ promote to global)
- add-rule — manually add a learning
"""

import os
import re
import subprocess
import sys


SKIP_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".lock",
                   ".css", ".svg", ".png", ".jpg", ".gif", ".xml"}


def cmd_train(args):
    """Train a LoRA adapter for an agent."""
    from planet_maiko.brain.learning.trainer import train_agent, check_requirements

    if args.check:
        reqs = check_requirements()
        print(f"Backend: {reqs.get('backend', 'none')}")
        print(f"Ready: {reqs.get('ready', False)}")
        if not reqs.get("ready"):
            print(f"Install: {reqs.get('recommendation', '')}")
        return

    from planet_maiko.app import create_app
    app = create_app(start_scheduler=False)
    with app.app_context():
        if args.all:
            from planet_maiko.models.agent_profile import AgentProfile
            profiles = AgentProfile.query.filter(
                (AgentProfile.archived == False) | (AgentProfile.archived == None)  # noqa: E711, E712
            ).all()
            for p in profiles:
                print(f"\nTraining {p.display_name}...")
                result = train_agent(p.id)
                if result.get("success"):
                    print(f"  Done: {result['adapter_path']} ({result['duration_seconds']}s)")
                else:
                    print(f"  Failed: {result.get('error')}")
        elif args.agent:
            result = train_agent(args.agent)
            if result.get("success"):
                print(f"Done: {result['adapter_path']} ({result['examples']} examples, {result['duration_seconds']}s)")
            else:
                print(f"Failed: {result.get('error')}")
                if result.get("install_hint"):
                    print(f"Install: {result['install_hint']}")
        else:
            print("Specify an agent ID or --all. Use --check to verify requirements.")


def cmd_extract_training_data(args):
    """Extract training data from PR review history."""
    from planet_maiko.config import load_config
    from planet_maiko.brain.learning.training_data import extract_training_data

    config = load_config()
    repos = config.get("github", {}).get("repos", [])
    if not repos:
        print("No repos configured. Set them in Settings > GitHub.")
        return

    print(f"Extracting from {len(repos)} repos (limit {args.limit} PRs each)...")
    result = extract_training_data(repos=repos, limit_per_repo=args.limit)
    print(f"Extracted {result['pairs']} training pairs:")
    print(f"  Violations: {result['violations']}")
    print(f"  Passes: {result['passes']}")
    if result.get("file_path"):
        print(f"  Saved to: {result['file_path']}")


def cmd_generate_synthetic(args):
    """Generate synthetic training data using Opus."""
    from planet_maiko.brain.learning.synthetic_data import generate_synthetic_dataset

    print(f"Generating synthetic training data (limit: {args.limit or 'all'})...")
    print("This sends diffs to Opus in batches of 5 for structured review.")
    print()

    result = generate_synthetic_dataset(
        input_dataset=args.input,
        limit=args.limit,
    )

    if result.get("success"):
        print(f"Generated {result['pairs']} training pairs:")
        print(f"  Violations: {result['violations']}")
        print(f"  Passes: {result['passes']}")
        print(f"  Batches: {result['batches']} ({result['errors']} errors)")
        if result.get("file_path"):
            print(f"  Saved to: {result['file_path']}")
    else:
        print(f"Failed: {result.get('error')}")


def cmd_generate_rules(args):
    """Generate training data from active learnings."""
    from planet_maiko.app import create_app
    app = create_app(start_scheduler=False)
    with app.app_context():
        from planet_maiko.brain.learning.rule_training_data import generate_rule_dataset

        print(f"Generating training data from active learnings ({args.examples} examples per rule)...")
        print("For each rule: real signals + synthetic violations + synthetic passes")
        print()

        result = generate_rule_dataset(examples_per_rule=args.examples)

        if result.get("success"):
            print(f"Generated {result['pairs']} training pairs from {result['rules_processed']} rules:")
            print(f"  Violations: {result['violations']}")
            print(f"  Passes: {result['passes']}")
            if result['errors']:
                print(f"  Errors: {result['errors']}")
            if result.get("file_path"):
                print(f"  Saved to: {result['file_path']}")
        else:
            print(f"Failed: {result.get('error')}")


def cmd_retrain(args):
    """Retrain LoRA adapter with feedback loop: resolve → generate → train."""
    from planet_maiko.app import create_app
    app = create_app(start_scheduler=False)
    with app.app_context():
        repo = args.repo
        safe_repo = repo.replace("/", "--") if repo else "default"
        agent_id = f"lora-{safe_repo}"

        # Step 1: Resolve & sync feedback
        if not args.skip_feedback:
            from planet_maiko.brain.learning.feedback import resolve_pending_feedback, sync_feedback_to_server, drain_signal_queue
            from planet_maiko.brain.learning.processor import process_signals

            print("Step 1/3: Resolving feedback...")
            repo_path = args.repo_path
            fb = resolve_pending_feedback(repo=repo, repo_path=repo_path)
            print(f"  Resolved: {fb['accepted']} accepted, {fb['rejected']} rejected, {fb['still_pending']} pending")

            sync = sync_feedback_to_server()
            print(f"  Synced {sync['synced']} signals to database")

            drained = drain_signal_queue()
            if drained["imported"]:
                print(f"  Imported {drained['imported']} queued signals from CLI corrections")

            result = process_signals()
            if result["graduated"]:
                print(f"  {result['graduated']} learnings graduated")
            if result["new_learnings"]:
                print(f"  {result['new_learnings']} new learnings created")
        else:
            print("Step 1/3: Skipping feedback resolution")

        # Step 2: Generate training data (incremental)
        if not args.skip_datagen:
            from planet_maiko.brain.learning.rule_training_data import generate_rule_dataset, get_covered_rule_ids
            from planet_maiko.models.learning import Learning

            print("\nStep 2/3: Generating training data...")

            # Find rules that don't have training data yet
            # Include both repo-scoped and global rules
            covered = get_covered_rule_ids(repo=repo)
            from sqlalchemy import or_
            if repo:
                all_active = Learning.query.filter(
                    Learning.status == "active",
                    or_(Learning.scope_repo == repo, Learning.scope_repo.is_(None)),
                ).all()
            else:
                all_active = Learning.query.filter_by(status="active").all()
            new_ids = [l.id for l in all_active if l.id not in covered]

            if new_ids:
                print(f"  {len(new_ids)} new rules to synthesize ({len(covered)} already covered)")
                result = generate_rule_dataset(
                    examples_per_rule=args.examples,
                    rule_ids=new_ids,
                    repo=repo,
                )
                if result.get("success"):
                    print(f"  Generated {result['pairs']} pairs from {result['rules_processed']} rules")
                    if result.get("file_path"):
                        print(f"  Saved to: {result['file_path']}")
                else:
                    print(f"  Warning: {result.get('error')}")
            else:
                print(f"  All {len(covered)} active rules already have training data")
                if args.force:
                    print("  --force: regenerating all rules")
                    result = generate_rule_dataset(examples_per_rule=args.examples, repo=repo)
                    if result.get("success"):
                        print(f"  Generated {result['pairs']} pairs from {result['rules_processed']} rules")
        else:
            print("\nStep 2/3: Skipping data generation")

        # Step 3: Train LoRA
        print(f"\nStep 3/3: Training LoRA adapter ({agent_id})...")
        from planet_maiko.brain.learning.trainer import train_agent
        result = train_agent(agent_profile_id=agent_id, repo=repo)

        if result.get("success"):
            print(f"  Done: {result['adapter_path']}")
            print(f"  {result['examples']} examples, {result['duration_seconds']}s")
            print(f"\nAdapter ready. Pre-commit hook will pick it up automatically.")
        else:
            print(f"  Failed: {result.get('error')}")
            if result.get("install_hint"):
                print(f"  Install: {result['install_hint']}")


def cmd_eval(args):
    """Evaluate a LoRA adapter's precision/recall on held-out data."""
    from planet_maiko.brain.learning.lora_eval import evaluate_adapter

    print("Evaluating adapter...")
    result = evaluate_adapter(
        adapter_path=args.adapter,
        repo=args.repo,
        holdout_fraction=args.holdout,
    )

    if not result.get("success"):
        print(f"Error: {result.get('error')}")
        return

    print(f"\n=== LoRA Evaluation ===")
    print(f"Adapter: {result['adapter_path']}")
    print(f"Test examples: {result['test_count']}")
    print(f"Precision: {result['precision']:.1%}")
    print(f"Recall:    {result['recall']:.1%}")
    print(f"F1:        {result['f1']:.1%}")

    if args.per_category and result.get("per_category"):
        print(f"\nPer-category breakdown:")
        for cat, metrics in sorted(result["per_category"].items()):
            print(f"  {cat:20s}  P={metrics['precision']:.0%}  R={metrics['recall']:.0%}  F1={metrics['f1']:.0%}  n={metrics['count']}")


def cmd_lora_feedback(args):
    """Report a LoRA false positive — records a corrective PASS training pair."""
    from planet_maiko.brain.learning.feedback import add_corrective_pass

    # Read code from --file or stdin
    if args.file:
        with open(args.file) as f:
            code = f.read()
        file_path = args.file
    elif args.code:
        code = args.code
        file_path = None
    else:
        if sys.stdin.isatty():
            print("Paste the code that was incorrectly flagged (Ctrl+D when done):", file=sys.stderr)
        code = sys.stdin.read()
        file_path = None

    if not code.strip():
        print("Error: No code provided.", file=sys.stderr)
        sys.exit(1)

    result = add_corrective_pass(
        code=code,
        file_path=file_path,
        repo=args.repo,
        model_output=args.output,
    )

    if result.get("success"):
        print(f"Recorded corrective PASS → {result['file_path']}")
        print("This will be picked up on the next retrain.")
    else:
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def cmd_lora_miss(args):
    """Report a LoRA false negative — records a corrective VIOLATION training pair."""
    from planet_maiko.brain.learning.feedback import add_corrective_violation

    # Read code from --file or stdin
    if args.file:
        with open(args.file) as f:
            code = f.read()
        file_path = args.file
    elif args.code:
        code = args.code
        file_path = None
    else:
        if sys.stdin.isatty():
            print("Paste the diff chunk the model missed (Ctrl+D when done):", file=sys.stderr)
        code = sys.stdin.read()
        file_path = None

    if not code.strip():
        print("Error: No code provided.", file=sys.stderr)
        sys.exit(1)

    result = add_corrective_violation(
        code=code,
        violation=args.violation,
        category=args.category,
        file_path=file_path,
        repo=args.repo,
    )

    if result.get("success"):
        print(f"Recorded corrective VIOLATION → {result['file_path']}")
        print("This will be picked up on the next retrain.")
    else:
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def cmd_dedup(args):
    """Merge semantically duplicate learnings."""
    from planet_maiko.app import create_app
    app = create_app()

    with app.app_context():
        from planet_maiko.brain.learning.classifier import dedup_learnings, promote_global_rules
        prefix = "[DRY RUN] " if args.dry_run else ""

        print("Phase 1: Within-repo dedup...")
        result = dedup_learnings(dry_run=args.dry_run)
        print(f"{prefix}Groups checked: {result['groups_checked']}")
        print(f"{prefix}Merges: {result['merges']}")
        print(f"{prefix}Dismissed: {result['dismissed']}")

        if args.promote_global:
            print("\nPhase 2: Cross-repo promotion to global rules...")
            promo = promote_global_rules(dry_run=args.dry_run)
            print(f"{prefix}Groups checked: {promo['groups_checked']}")
            print(f"{prefix}Promoted to global: {promo['promoted']}")
            print(f"{prefix}Dismissed (merged into global): {promo['dismissed']}")


def cmd_add_rule(args):
    """Manually add a learning rule."""
    from planet_maiko.app import create_app
    app = create_app()

    with app.app_context():
        from planet_maiko.database import db
        from planet_maiko.models.learning import Learning

        learning = Learning(
            rule=args.rule,
            category=args.category,
            scope_repo=args.repo,  # None = global
            scope_language=args.language,
            confidence=1.0,
            source="manual",
            status="active",
        )
        db.session.add(learning)
        db.session.commit()

        scope = args.repo or "global (all repos)"
        print(f"Added [{args.category}] rule (scope: {scope}):")
        print(f"  {args.rule}")


def cmd_review(args):
    """Review code using a trained LoRA adapter."""
    from planet_maiko.brain.learning.trainer import review_code

    if args.pr:
        _review_pr(args)
        return

    # Read code from file or stdin
    if args.file:
        with open(args.file) as f:
            code = f.read()
        file_path = args.file
    else:
        if sys.stdin.isatty():
            print("Paste code to review (Ctrl+D when done):", file=sys.stderr)
        code = sys.stdin.read()
        file_path = None

    result = review_code(
        code=code,
        agent_profile_id=args.agent,
        file_path=file_path,
    )

    if result.get("success"):
        print(f"\n{result['output']}")
    else:
        print(f"Error: {result.get('error')}")


def _parse_pr_url(url):
    """Extract owner/repo and PR number from a GitHub PR URL.

    Accepts:
        https://github.com/Org/Repo/pull/123
        https://github.example.com/Org/Repo/pull/123
        Org/Repo#123
    """
    # Full URL form
    m = re.match(r"https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)", url)
    if m:
        return m.group(1), int(m.group(2))

    # Short form: Org/Repo#123
    m = re.match(r"([^/]+/[^/]+)#(\d+)", url)
    if m:
        return m.group(1), int(m.group(2))

    return None, None


def _review_pr(args):
    """Review each file in a GitHub PR individually through the LoRA model."""
    from planet_maiko.brain.learning.trainer import review_code

    repo, pr_number = _parse_pr_url(args.pr)
    if not repo or not pr_number:
        print(f"Error: Could not parse PR URL: {args.pr}", file=sys.stderr)
        sys.exit(1)

    # Fetch diff via gh CLI
    cmd = ["gh", "pr", "diff", str(pr_number), "--repo", repo]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"Error fetching PR diff: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    diff = result.stdout

    # Split into per-file diffs, skip non-code files
    file_diffs = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    code_files = []
    for fd in file_diffs:
        fd = fd.strip()
        if not fd.startswith("diff --git"):
            continue
        match = re.search(r" b/(.+)$", fd.split("\n", 1)[0])
        fp = match.group(1) if match else "unknown"
        ext = os.path.splitext(fp)[1].lower()
        if ext in SKIP_EXTENSIONS:
            continue
        code_files.append((fp, fd))

    if not code_files:
        print("No code files to review in this PR.")
        return

    print(f"Reviewing {len(code_files)} files from {repo}#{pr_number}...\n")

    passed = 0
    flagged = 0

    for fp, fd in code_files:
        r = review_code(code=fd, adapter_path=None, agent_profile_id=args.agent, file_path=fp)

        if not r.get("success"):
            short = fp.rsplit("/", 1)[-1]
            print(f"  ERROR | {short}: {r.get('error', 'unknown error')}")
            continue

        # Strip mlx_lm noise from output
        output = r.get("output", "")
        verdict_lines = []
        for line in output.split("\n"):
            line_s = line.strip()
            if not line_s:
                continue
            if line_s.startswith(("Calling `python", "Prompt:", "Generation:", "Peak memory:")) or line_s == "==========":
                continue
            verdict_lines.append(line_s)
        verdict = "\n".join(verdict_lines)

        short = fp.rsplit("/", 1)[-1]
        if verdict.startswith("PASS"):
            passed += 1
            print(f"  \033[32mPASS\033[0m | {short}")
        else:
            flagged += 1
            # Print first line as summary, rest indented
            lines = verdict.split("\n")
            print(f"  \033[31mFLAG\033[0m | {short}: {lines[0]}")
            for extra in lines[1:]:
                if extra.strip():
                    print(f"         {extra}")

    print(f"\n--- {passed}/{passed + flagged} files passed ---")


def register(subparsers):
    """Register LoRA training/eval/feedback subcommands."""
    # maiko train
    p = subparsers.add_parser("train", help="Train a LoRA adapter for an agent")
    p.add_argument("agent", nargs="?", help="Agent ID or display name (omit for --check)")
    p.add_argument("--check", action="store_true", help="Check if training is available")
    p.add_argument("--all", action="store_true", help="Train all agents")
    p.set_defaults(func=cmd_train)

    # maiko extract-training-data
    p = subparsers.add_parser("extract-training-data", help="Extract training data from PR history")
    p.add_argument("--limit", type=int, default=200, help="Max PRs per repo")
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

    # maiko eval
    p = subparsers.add_parser("eval", help="Evaluate a LoRA adapter")
    p.add_argument("--adapter", help="Adapter path (uses most recent if omitted)")
    p.add_argument("--repo", help="Filter test data to this repo")
    p.add_argument("--holdout", type=float, default=0.2, help="Fraction of data to hold out for testing (default 0.2)")
    p.add_argument("--per-category", action="store_true", help="Show per-category breakdown")
    p.set_defaults(func=cmd_eval)

    # maiko review
    p = subparsers.add_parser("review", help="Review code using a trained LoRA adapter")
    p.add_argument("file", nargs="?", help="File to review (reads stdin if omitted)")
    p.add_argument("--pr", help="GitHub PR URL or Org/Repo#123 — reviews each file individually")
    p.add_argument("--agent", help="Agent ID (uses most recent adapter if omitted)")
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
    p.add_argument("--dry-run", action="store_true", help="Report what would be merged without applying")
    p.add_argument("--promote-global", action="store_true", help="Also promote cross-repo duplicates to global rules")
    p.set_defaults(func=cmd_dedup)

    # maiko add-rule
    p = subparsers.add_parser("add-rule", help="Manually add a learning rule")
    p.add_argument("rule", help="The rule text")
    p.add_argument("--category", "-c", default="domain_knowledge",
                   help="Category (default: domain_knowledge)")
    p.add_argument("--repo", help="Scope to a specific repo (omit for global)")
    p.add_argument("--language", help="Scope to a specific language")
    p.set_defaults(func=cmd_add_rule)
