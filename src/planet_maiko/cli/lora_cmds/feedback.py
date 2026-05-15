"""Feedback subcommands for the maiko LoRA CLI."""

import os
import re
import subprocess
import sys


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

