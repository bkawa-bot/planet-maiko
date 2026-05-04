"""Training subcommands for the maiko LoRA CLI.

Originally lived in cli/lora_cmds.py — extracted into a per-family
file so each command's imports + helpers stay close to it.
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

