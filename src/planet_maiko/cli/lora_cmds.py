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

    exclude_urls = []
    if args.exclude_from:
        import json as _json
        with open(args.exclude_from, encoding="utf-8") as f:
            fixture = _json.load(f)
        for entry in fixture.get("prs") or []:
            url = (entry.get("url") or "").strip()
            if url:
                exclude_urls.append(url)
        if exclude_urls:
            print(f"Excluding {len(exclude_urls)} holdout PRs from training data.")

    print(f"Extracting from {len(repos)} repos (limit {args.limit} PRs each)...")
    result = extract_training_data(
        repos=repos,
        limit_per_repo=args.limit,
        exclude_pr_urls=exclude_urls or None,
    )
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
            from planet_maiko.brain.learning.clustering import cluster_signals_into_learnings

            print("Step 1/3: Resolving feedback...")
            repo_path = args.repo_path
            fb = resolve_pending_feedback(repo=repo, repo_path=repo_path)
            print(f"  Resolved: {fb['accepted']} accepted, {fb['rejected']} rejected, {fb['still_pending']} pending")

            sync = sync_feedback_to_server()
            print(f"  Synced {sync['synced']} signals to database")

            drained = drain_signal_queue()
            if drained["imported"]:
                print(f"  Imported {drained['imported']} queued signals from CLI corrections")

            result = cluster_signals_into_learnings()
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
                    or_(
                        Learning.scope_repo == repo,
                        Learning.is_global == True,  # noqa: E712
                        Learning.scope_repo.is_(None),
                    ),
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


def cmd_eval_prs(args):
    """PR-level holdout eval: run trained model on real PRs + compare to human reviews."""
    import os as _os
    from planet_maiko.paths import data_dir
    from planet_maiko.eval import holdout
    from planet_maiko.app import create_app

    fixture_path = args.fixture
    if not _os.path.isfile(fixture_path):
        print(f"Fixture not found: {fixture_path}", file=sys.stderr)
        print("Tip: copy src/planet_maiko/eval/fixtures/pr-review-v1.example.json "
              "to pr-review-v1.json and fill in real PR URLs.", file=sys.stderr)
        sys.exit(1)

    adapter_path = args.adapter
    if not adapter_path:
        # Same fallback review_code uses — most recent adapter dir.
        models_dir = _os.path.join(data_dir(), "models")
        if _os.path.isdir(models_dir):
            candidates = sorted(_os.listdir(models_dir), reverse=True)
            if candidates:
                adapter_path = _os.path.join(models_dir, candidates[0])
    if not adapter_path:
        print("No adapter path given and no adapters found in data/models/.", file=sys.stderr)
        sys.exit(1)

    def _progress(ev):
        if ev["event"] == "pr_start":
            print(f"  [running] {ev['pr']}", flush=True)
        elif ev["event"] == "pr_done":
            base = f"flagged {ev['flagged_with']}/{ev['human_files']} human-flagged files"
            if ev.get("flagged_without") is not None:
                base += f" (baseline: {ev['flagged_without']})"
            if ev.get("semantic_hits_with") is not None:
                base += f" — {ev['semantic_hits_with']} judge-confirmed"
            if ev.get("errors"):
                base += f" — {ev['errors']} inference errors"
            print(f"  [done   ] {ev['pr']}: {base}", flush=True)
        elif ev["event"] == "judge_call":
            mark = "MATCH" if ev["match"] else "miss"
            print(f"    judge [{mark}] {ev['file']}", flush=True)

    print(f"Fixture: {fixture_path}")
    print(f"Adapter: {adapter_path}")
    print(f"Match mode: {args.match_mode}")
    if args.compare_baseline:
        print("Mode: with-adapter + baseline (will run each PR twice)")
    if args.refresh_ground_truth:
        print("Refreshing ground-truth cache from GitHub.")
    print()

    # Judge mode needs the Flask app context for runtime lookup.
    if args.match_mode == "judge":
        app = create_app(start_scheduler=False)
        ctx = app.app_context()
        ctx.push()
    else:
        ctx = None

    try:
        result = holdout.run(
            fixture_path=fixture_path,
            adapter_path=adapter_path,
            compare_baseline=args.compare_baseline,
            progress=_progress,
            match_mode=args.match_mode,
            refresh_ground_truth=args.refresh_ground_truth,
        )
    finally:
        if ctx is not None:
            ctx.pop()

    against = None
    if args.against:
        if not _os.path.isfile(args.against):
            print(f"Against-file not found: {args.against}", file=sys.stderr)
        else:
            against = holdout.diff_against(result, args.against)

    report = holdout.format_report(result, against=against)

    # Write the report to data_dir so it survives. Also write a JSON
    # sibling so metric-tracking scripts can diff runs without parsing
    # markdown. Print to stdout so it's immediately visible.
    import json as _json
    reports_dir = _os.path.join(data_dir(), "eval-reports")
    _os.makedirs(reports_dir, exist_ok=True)
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    out_path = args.output or _os.path.join(reports_dir, f"holdout-{ts}.md")
    json_path = _os.path.splitext(out_path)[0] + ".json"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(json_path, "w", encoding="utf-8") as f:
        _json.dump(holdout.to_json(result), f, indent=2, ensure_ascii=False)

    print()
    print(report)
    print()
    print(f"Report saved: {out_path}")
    print(f"JSON saved:   {json_path}")


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
    """Merge semantically duplicate learnings via LLM clustering.

    Global promotion now happens automatically — signals from 3+
    distinct repos flip a learning's is_global flag during the normal
    cluster pass (see brain.learning.clustering._maybe_promote_global).
    No separate --promote-global phase is needed.
    """
    from planet_maiko.app import create_app
    app = create_app()

    with app.app_context():
        from planet_maiko.brain.learning.clustering import cluster_learnings

        if args.dry_run:
            print("[DRY RUN] not supported by cluster_learnings — it commits merges as it goes.")
            print("Re-run without --dry-run to apply, or inspect the Knowledge tab first.")
            return

        result = cluster_learnings()
        print(f"Categories scanned: {result['categories_scanned']}")
        print(f"Clusters processed: {result['clusters_processed']}")
        print(f"Learnings merged:   {result['learnings_merged']}")
        print(f"Skipped (singletons): {result['skipped']}")


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
