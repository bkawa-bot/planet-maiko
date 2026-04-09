#!/usr/bin/env python3
"""maiko CLI - communicate with Planet Maiko from anywhere.

Used by agents (and humans) to report status back to Planet Maiko.

Usage:
    maiko report "Status message here"
    maiko task done [task-id]
    maiko task start [task-id]
    maiko pupdate create --title "Title" --body "Body" --priority normal
    maiko status
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

import os
from planet_maiko.config import MAIKO_PORT, maiko_api_url
MAIKO_API = maiko_api_url()


def api_request(path, method="GET", data=None):
    """Make a request to the Planet Maiko API."""
    url = f"{MAIKO_API}{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"Error: Could not connect to Planet Maiko at {MAIKO_API}", file=sys.stderr)
        print(f"  Is the server running? (python3 app.py)", file=sys.stderr)
        sys.exit(1)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Error: {e.code} - {body}", file=sys.stderr)
        sys.exit(1)


def _detect_task_id():
    """Try to detect the task ID from the current directory's TASK.md."""
    try:
        with open("TASK.md") as f:
            for line in f:
                if line.startswith("**Task ID:**"):
                    return line.split("**Task ID:**")[1].strip()
    except FileNotFoundError:
        pass
    return None


def cmd_report(args):
    """Report a status message to Planet Maiko."""
    task_id = args.task or _detect_task_id()
    tags = [task_id] if task_id else []

    pupdate = {
        "id": f"agent-report-{int(time.time())}",
        "source": "agent",
        "type": args.type,
        "priority": args.priority,
        "title": f"[Agent] {args.message}",
        "body": args.body or "",
        "tags": tags,
    }

    result = api_request("/pupdates", method="POST", data=pupdate)
    print(f"Reported: {args.message}")
    if task_id:
        print(f"  Task: {task_id}")


def cmd_task(args):
    """Update a task's status."""
    task_id = args.task_id or _detect_task_id()
    if not task_id:
        print("Error: No task ID provided and could not detect from TASK.md", file=sys.stderr)
        sys.exit(1)

    action = args.action
    if action == "done":
        # Report completion via pupdate so the monitor picks it up
        pupdate = {
            "id": f"agent-done-{task_id}-{int(time.time())}",
            "source": "agent",
            "type": "agent_done",
            "priority": "normal",
            "title": f"[Agent] Task completed: {task_id}",
            "body": args.message or "Task completed successfully.",
            "tags": [task_id],
        }
        api_request("/pupdates", method="POST", data=pupdate)
        api_request(f"/tasks/{task_id}/done", method="POST")
        print(f"Task {task_id} marked as done")
    elif action == "start":
        api_request(f"/tasks/{task_id}/start", method="POST")
        print(f"Task {task_id} marked as in progress")
    elif action == "stuck":
        pupdate = {
            "id": f"agent-stuck-{task_id}-{int(time.time())}",
            "source": "agent",
            "type": "agent_stuck",
            "priority": "high",
            "title": f"[Agent] Stuck on: {task_id}",
            "body": args.message or "Agent needs help.",
            "tags": [task_id],
        }
        api_request("/pupdates", method="POST", data=pupdate)
        print(f"Reported stuck on {task_id}")


def cmd_inbox(args):
    """Check for messages from Planet Maiko."""
    task_id = args.task or _detect_task_id()
    if not task_id:
        print("Error: No task ID provided and could not detect from TASK.md", file=sys.stderr)
        sys.exit(1)

    params = "?unread_only=true&mark_read=true"
    if args.all:
        params = "?unread_only=false&mark_read=false"

    messages = api_request(f"/agents/{task_id}/inbox{params}")

    if not messages:
        print("No new messages.")
        return

    for msg in messages:
        sender = msg["sender"]
        mtype = msg["message_type"]
        time = msg["created_at"][:16].replace("T", " ")
        content = msg["content"]
        print(f"[{time}] ({sender}/{mtype}) {content}")


def cmd_reply(args):
    """Send a message back to Planet Maiko."""
    task_id = args.task or _detect_task_id()
    if not task_id:
        print("Error: No task ID provided and could not detect from TASK.md", file=sys.stderr)
        sys.exit(1)

    data = {
        "content": args.message,
        "message_type": args.type,
    }
    api_request(f"/agents/{task_id}/outbox", method="POST", data=data)
    print(f"Sent reply for {task_id}")


def cmd_feedback(args):
    """Send in-session feedback about agent work."""
    task_id = args.task or _detect_task_id()
    if not task_id:
        print("Error: Could not detect task ID. Use --task to specify.")
        return

    # Create a Signal directly with code context if provided
    signal_data = {
        "category": args.category,
        "text": args.message,
        "source_type": "session_feedback",
        "severity": args.severity,
    }

    # Read code from --code flag or --file
    if args.code:
        signal_data["code_context"] = args.code
    elif args.file:
        try:
            with open(args.file) as f:
                signal_data["code_context"] = f.read()[:3000]
            signal_data["file_path"] = args.file
        except Exception:
            pass

    try:
        api_request("/signals", method="POST", data=signal_data)
    except SystemExit:
        pass  # Server might not be running — still send via outbox

    # Also send via agent outbox for dashboard visibility
    data = {
        "content": args.message,
        "message_type": "feedback",
        "sender": "agent",
        "metadata": {
            "feedback_category": args.category,
            "feedback_severity": args.severity,
        }
    }
    api_request(f"/agents/{task_id}/outbox", method="POST", data=data)
    print(f"Feedback recorded for {task_id} [{args.category}]")


def cmd_sleep(args):
    """Put an agent to sleep."""
    task_id = args.task or _detect_task_id()
    if not task_id:
        print("Error: Could not detect task ID. Use --task.")
        return
    data = {"content": "Going to sleep.", "message_type": "agent_sleep", "sender": "agent"}
    api_request(f"/agents/{task_id}/outbox", method="POST", data=data)
    print(f"Agent sleeping. Wake with: maiko wake agent-{task_id}")


def cmd_wake(args):
    """Wake a sleeping agent."""
    agent_id = args.agent_id
    # Send a wake pupdate
    data = {
        "id": f"wake-{agent_id}-{int(time.time())}",
        "source": "maiko",
        "type": "agent_wake",
        "priority": "normal",
        "title": f"Wake up, {agent_id}!",
        "body": "Time to get back to work. Check your inbox for updates.",
        "tags": [agent_id, "wake"],
    }
    api_request("/pupdates", method="POST", data=data)
    print(f"Wake signal sent to {agent_id}")


def cmd_status(args):
    """Show system health status."""
    import subprocess

    print("=== Planet Maiko Status ===\n")

    # Check backend
    try:
        data = api_request("/brain/status")
        print(f"Backend: running on :{MAIKO_PORT}")
        print(f"  Brain cycles: {data.get('cycle_count', 0)}")
        last = data.get('last_cycle')
        print(f"  Last cycle: {last or 'never'}")
    except SystemExit:
        # api_request calls sys.exit on failure; catch it here to continue
        print("Backend: NOT running (start with: maiko serve)")

    # Check gh CLI
    try:
        result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=5)
        print(f"gh CLI: {'authenticated' if result.returncode == 0 else 'not authenticated'}")
    except FileNotFoundError:
        print("gh CLI: not installed")

    # Check config
    try:
        from planet_maiko.config import load_config
        config = load_config()
        username = config.get("github", {}).get("username", "")
        repos = config.get("github", {}).get("repos", [])
        location = config.get("scene", {}).get("location_name", "")
        print(f"Config: {'configured' if username else 'needs setup (run: maiko setup)'}")
        if username:
            print(f"  GitHub: {username} ({len(repos)} repo(s))")
        if location:
            print(f"  Location: {location}")
    except Exception:
        print("Config: error loading")

    # Check database
    try:
        from planet_maiko.paths import data_dir
        import os
        db_path = os.path.join(data_dir(), "maiko.db")
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            print(f"Database: {db_path} ({size_mb:.1f} MB)")
        else:
            print(f"Database: not created yet (start server first)")
    except Exception:
        print("Database: error checking")

    print()


def cmd_setup(args):
    """Interactive first-time setup."""
    from planet_maiko.config import load_config, save_config
    import subprocess

    print("=== Planet Maiko Setup ===\n")

    config = load_config()

    # GitHub username
    current_user = config.get("github", {}).get("username", "")
    username = input(f"GitHub username [{current_user}]: ").strip() or current_user
    config.setdefault("github", {})["username"] = username
    config["github"]["enabled"] = True

    # Test gh CLI
    try:
        result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print("  gh CLI: authenticated")
        else:
            print("  gh CLI: not authenticated. Run 'gh auth login' first.")
    except FileNotFoundError:
        print("  gh CLI: not installed. Install from https://cli.github.com/")

    # Repos
    current_repos = config.get("github", {}).get("repos", [])
    repos_input = input(f"Repos (comma-separated) [{', '.join(current_repos)}]: ").strip()
    if repos_input:
        config["github"]["repos"] = [r.strip() for r in repos_input.split(",") if r.strip()]

    # Repo roots
    current_roots = config.get("github", {}).get("repo_roots", [])
    roots_input = input(f"Repo roots (local paths) [{', '.join(current_roots)}]: ").strip()
    if roots_input:
        config["github"]["repo_roots"] = [r.strip() for r in roots_input.split(",") if r.strip()]

    # Location
    location = input("City/zipcode for weather (or Enter to skip): ").strip()
    if location:
        try:
            import urllib.parse, json
            url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
                if data.get("results"):
                    r = data["results"][0]
                    name = f"{r['name']}, {r.get('admin1', '')}"
                    config.setdefault("scene", {})["latitude"] = r["latitude"]
                    config["scene"]["longitude"] = r["longitude"]
                    config["scene"]["location_name"] = name
                    print(f"  Location: {name} ({r['latitude']}, {r['longitude']})")
        except Exception as e:
            print(f"  Could not resolve location: {e}")

    save_config(config)
    print(f"\nConfig saved. Start with: maiko serve")


def cmd_serve(args):
    """Start the Planet Maiko server."""
    from planet_maiko.app import create_app
    print(f"Starting Planet Maiko on http://{args.host}:{args.port}")
    app = create_app(start_scheduler=True)
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


def cmd_desktop(args):
    """Launch Planet Maiko as a desktop application."""
    from planet_maiko.desktop import main as desktop_main
    print(f"Launching Planet Maiko desktop on http://{args.host}:{args.port}")
    desktop_main(host=args.host, port=args.port)


def cmd_seed(args):
    """Populate the database with realistic test data."""
    from planet_maiko.app import create_app
    from planet_maiko.seed import seed_data
    app = create_app(start_scheduler=False)
    seed_data(app)
    print("Seed data loaded.")


def cmd_bootstrap(args):
    """Bootstrap learnings from past PR reviews."""
    from planet_maiko.app import create_app
    app = create_app(start_scheduler=False)
    with app.app_context():
        from planet_maiko.brain.learning.bootstrap import bootstrap_from_prs
        result = bootstrap_from_prs(limit=args.limit)
        print(f"\nCreated {result['total_created']} signals total.\n")
        for r in result["per_repo"]:
            if r["error"]:
                print(f"  {r['repo']:50s}  ERROR: {r['error']}")
            else:
                print(f"  {r['repo']:50s}  {r['prs_scanned']} PRs scanned, {r['signals_created']} signals")


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
                (AgentProfile.archived == False) | (AgentProfile.archived == None)
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


SKIP_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".lock", ".css", ".svg", ".png", ".jpg", ".gif", ".xml"}


def _parse_pr_url(url):
    """Extract owner/repo and PR number from a GitHub PR URL.

    Accepts:
        https://github.com/Org/Repo/pull/123
        https://github.example.com/Org/Repo/pull/123
        Org/Repo#123
    """
    import re

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
    import os
    import re
    import subprocess
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


def main():
    parser = argparse.ArgumentParser(
        prog="maiko",
        description="Planet Maiko - Personal engineering intelligence dashboard",
    )
    subparsers = parser.add_subparsers(dest="command")

    # maiko report
    report_parser = subparsers.add_parser("report", help="Report status to Planet Maiko")
    report_parser.add_argument("message", help="Status message")
    report_parser.add_argument("--body", help="Detailed body text")
    report_parser.add_argument("--task", help="Task ID (auto-detected from TASK.md if omitted)")
    report_parser.add_argument("--priority", default="normal", choices=["low", "normal", "high", "urgent"])
    report_parser.add_argument("--type", default="agent_update", help="Pupdate type")
    report_parser.set_defaults(func=cmd_report)

    # maiko task
    task_parser = subparsers.add_parser("task", help="Update task status")
    task_parser.add_argument("action", choices=["done", "start", "stuck"])
    task_parser.add_argument("task_id", nargs="?", help="Task ID (auto-detected if omitted)")
    task_parser.add_argument("--message", "-m", help="Optional message")
    task_parser.set_defaults(func=cmd_task)

    # maiko inbox
    inbox_parser = subparsers.add_parser("inbox", help="Check for messages from Planet Maiko")
    inbox_parser.add_argument("--task", help="Task ID (auto-detected if omitted)")
    inbox_parser.add_argument("--all", action="store_true", help="Show all messages, not just unread")
    inbox_parser.set_defaults(func=cmd_inbox)

    # maiko reply
    reply_parser = subparsers.add_parser("reply", help="Send a message back to Planet Maiko")
    reply_parser.add_argument("message", help="Reply message")
    reply_parser.add_argument("--task", help="Task ID (auto-detected if omitted)")
    reply_parser.add_argument("--type", default="message", help="Message type")
    reply_parser.set_defaults(func=cmd_reply)

    # maiko feedback
    feedback_parser = subparsers.add_parser("feedback", help="Send in-session feedback about agent work")
    feedback_parser.add_argument("message", help="Feedback message")
    feedback_parser.add_argument("--category", default="pattern", help="Category: testing, security, error_handling, etc.")
    feedback_parser.add_argument("--severity", default="suggestion", help="suggestion, warning, or blocking")
    feedback_parser.add_argument("--code", help="Code snippet showing the pattern (before/after)")
    feedback_parser.add_argument("--file", help="File path to include as code context")
    feedback_parser.add_argument("--task", help="Task ID (auto-detected if in worktree)")
    feedback_parser.set_defaults(func=cmd_feedback)

    # maiko sleep
    sleep_parser = subparsers.add_parser("sleep", help="Put agent to sleep")
    sleep_parser.add_argument("--task", help="Task ID (auto-detected from TASK.md if omitted)")
    sleep_parser.set_defaults(func=cmd_sleep)

    # maiko wake
    wake_parser = subparsers.add_parser("wake", help="Wake a sleeping agent")
    wake_parser.add_argument("agent_id", help="Agent ID to wake")
    wake_parser.set_defaults(func=cmd_wake)

    # maiko setup
    setup_parser = subparsers.add_parser("setup", help="Interactive first-time setup")
    setup_parser.set_defaults(func=cmd_setup)

    # maiko status
    status_parser = subparsers.add_parser("status", help="Check Planet Maiko status")
    status_parser.set_defaults(func=cmd_status)

    # maiko serve
    serve_parser = subparsers.add_parser("serve", help="Start Planet Maiko server")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    serve_parser.add_argument("--port", type=int, default=MAIKO_PORT, help="Port to listen on")
    serve_parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    serve_parser.set_defaults(func=cmd_serve)

    # maiko desktop
    desktop_parser = subparsers.add_parser("desktop", help="Launch Planet Maiko as a desktop app")
    desktop_parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    desktop_parser.add_argument("--port", type=int, default=MAIKO_PORT, help="Port to listen on")
    desktop_parser.set_defaults(func=cmd_desktop)

    # maiko seed
    seed_parser = subparsers.add_parser("seed", help="Populate database with test data")
    seed_parser.set_defaults(func=cmd_seed)

    # maiko bootstrap
    bootstrap_parser = subparsers.add_parser("bootstrap", help="Seed learnings from past PR reviews")
    bootstrap_parser.add_argument("--limit", type=int, default=20, help="Max PRs to scan")
    bootstrap_parser.set_defaults(func=cmd_bootstrap)

    # maiko train
    train_parser = subparsers.add_parser("train", help="Train a LoRA adapter for an agent")
    train_parser.add_argument("agent", nargs="?", help="Agent ID or display name (omit for --check)")
    train_parser.add_argument("--check", action="store_true", help="Check if training is available")
    train_parser.add_argument("--all", action="store_true", help="Train all agents")
    train_parser.set_defaults(func=cmd_train)

    # maiko extract-training-data
    extract_parser = subparsers.add_parser("extract-training-data", help="Extract training data from PR history")
    extract_parser.add_argument("--limit", type=int, default=200, help="Max PRs per repo")
    extract_parser.set_defaults(func=cmd_extract_training_data)

    # maiko generate-rules
    rules_parser = subparsers.add_parser("generate-rules", help="Generate training data from active learnings")
    rules_parser.add_argument("--examples", type=int, default=50, help="Examples per rule (default 50)")
    rules_parser.set_defaults(func=cmd_generate_rules)

    # maiko generate-synthetic
    synth_parser = subparsers.add_parser("generate-synthetic", help="Generate synthetic training data via Opus")
    synth_parser.add_argument("--input", help="Input JSONL dataset (uses latest if omitted)")
    synth_parser.add_argument("--limit", type=int, help="Max pairs to process")
    synth_parser.set_defaults(func=cmd_generate_synthetic)

    # maiko retrain
    retrain_parser = subparsers.add_parser("retrain", help="Retrain LoRA adapter with feedback loop")
    retrain_parser.add_argument("repo", nargs="?", help="Repo name (e.g. org/repo)")
    retrain_parser.add_argument("--repo-path", help="Local repo path for git log resolution")
    retrain_parser.add_argument("--skip-feedback", action="store_true", help="Skip feedback resolution step")
    retrain_parser.add_argument("--skip-datagen", action="store_true", help="Skip training data generation")
    retrain_parser.add_argument("--force", action="store_true", help="Regenerate data for all rules, not just new ones")
    retrain_parser.add_argument("--examples", type=int, default=50, help="Examples per rule (default 50)")
    retrain_parser.set_defaults(func=cmd_retrain)

    # maiko eval
    eval_parser = subparsers.add_parser("eval", help="Evaluate a LoRA adapter")
    eval_parser.add_argument("--adapter", help="Adapter path (uses most recent if omitted)")
    eval_parser.add_argument("--repo", help="Filter test data to this repo")
    eval_parser.add_argument("--holdout", type=float, default=0.2, help="Fraction of data to hold out for testing (default 0.2)")
    eval_parser.add_argument("--per-category", action="store_true", help="Show per-category breakdown")
    eval_parser.set_defaults(func=cmd_eval)

    # maiko review
    review_parser = subparsers.add_parser("review", help="Review code using a trained LoRA adapter")
    review_parser.add_argument("file", nargs="?", help="File to review (reads stdin if omitted)")
    review_parser.add_argument("--pr", help="GitHub PR URL or Org/Repo#123 — reviews each file individually")
    review_parser.add_argument("--agent", help="Agent ID (uses most recent adapter if omitted)")
    review_parser.set_defaults(func=cmd_review)

    # maiko lora-feedback
    lora_fb_parser = subparsers.add_parser("lora-feedback", help="Report a LoRA false positive (corrective PASS)")
    lora_fb_parser.add_argument("--file", "-f", help="File that was incorrectly flagged")
    lora_fb_parser.add_argument("--code", "-c", help="Code snippet that was incorrectly flagged")
    lora_fb_parser.add_argument("--repo", help="Repo name (e.g. org/repo)")
    lora_fb_parser.add_argument("--output", "-o", help="The incorrect model output (for logging)")
    lora_fb_parser.set_defaults(func=cmd_lora_feedback)

    # maiko lora-miss
    lora_miss_parser = subparsers.add_parser("lora-miss", help="Report a LoRA false negative (model missed a violation)")
    lora_miss_parser.add_argument("--violation", "-v", required=True, help="Description of what should have been caught")
    lora_miss_parser.add_argument("--file", "-f", help="File containing the diff chunk")
    lora_miss_parser.add_argument("--code", "-c", help="Inline diff chunk the model missed")
    lora_miss_parser.add_argument("--category", help="Violation category (e.g. testing, security, architecture)")
    lora_miss_parser.add_argument("--repo", help="Repo name (e.g. org/repo)")
    lora_miss_parser.set_defaults(func=cmd_lora_miss)

    # maiko dedup
    dedup_parser = subparsers.add_parser("dedup", help="Merge semantically duplicate learnings")
    dedup_parser.add_argument("--dry-run", action="store_true", help="Report what would be merged without applying")
    dedup_parser.add_argument("--promote-global", action="store_true", help="Also promote cross-repo duplicates to global rules")
    dedup_parser.set_defaults(func=cmd_dedup)

    # maiko add-rule
    rule_parser = subparsers.add_parser("add-rule", help="Manually add a learning rule")
    rule_parser.add_argument("rule", help="The rule text")
    rule_parser.add_argument("--category", "-c", default="domain_knowledge",
                             help="Category (default: domain_knowledge)")
    rule_parser.add_argument("--repo", help="Scope to a specific repo (omit for global)")
    rule_parser.add_argument("--language", help="Scope to a specific language")
    rule_parser.set_defaults(func=cmd_add_rule)

    # Let plugins register CLI commands
    try:
        from planet_maiko.plugins.loader import discover_plugins
        for plugin in discover_plugins():
            plugin.register_commands(subparsers)
    except Exception:
        pass  # plugins may not be available in all contexts

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
