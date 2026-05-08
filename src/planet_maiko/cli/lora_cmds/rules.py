"""Rules subcommands for the maiko LoRA CLI.

Originally lived in cli/lora_cmds.py — extracted into a per-family
file so each command's imports + helpers stay close to it.
"""

import os
import re
import subprocess
import sys


def cmd_rules_regen(args):
    """Manually trigger the violation-description backfill. Pass
    --force to regenerate every active rule (useful after a prompt
    change). Runs in the background; logs progress to the Flask
    server's stderr."""
    from planet_maiko.app import create_app
    from planet_maiko.brain.learning.violation_backfill import (
        backfill_in_background,
        backfill_violation_descriptions,
    )

    app = create_app(start_scheduler=False)
    if args.foreground:
        # Run synchronously in this CLI process — useful for testing
        # / debugging since the output appears here, not in the Flask
        # server log.
        with app.app_context():
            result = backfill_violation_descriptions(force=args.force)
        print(
            f"Done. Succeeded: {result['succeeded']}, "
            f"Failed: {result['failed']}, Total: {result['total']}"
        )
    else:
        backfill_in_background(app, force=args.force)
        print(
            "Backfill kicked off on background thread. "
            "Watch the Flask server log for [violation-backfill] progress lines."
        )


def cmd_rule_show(args):
    """Print full metadata for one Learning, including the
    Claude-generated violation_description Maiko uses for RAG
    retrieval. Useful for spot-checking that a rule's description
    actually matches its intent."""
    import math
    from planet_maiko.app import create_app
    from planet_maiko.models.learning import Learning

    app = create_app(start_scheduler=False)
    with app.app_context():
        learning = Learning.query.get(args.id)
        if not learning:
            print(f"Learning #{args.id} not found.", file=sys.stderr)
            sys.exit(1)

        print(f"=== Learning #{learning.id} ===")
        print(f"Rule:       {learning.rule}")
        print(f"Category:   {learning.category}")
        print(f"Status:     {learning.status}")
        scope = learning.scope_repo or ("global" if learning.is_global else "(unscoped)")
        print(f"Scope:      {scope}")
        print(f"Signals:    {learning.signal_count or 0}")
        print(f"Confidence: {learning.confidence or 0:.2f}")
        print(f"Created:    {learning.created_at}")
        print(f"Updated:    {learning.updated_at}")
        print()

        if learning.violation_description:
            print("=== Violation description (Claude-generated) ===")
            print(learning.violation_description)
            print()
            if learning.violation_description_generated_at:
                print(f"Generated at:    {learning.violation_description_generated_at}")
            if learning.violation_description_signal_count is not None:
                gen_count = learning.violation_description_signal_count
                current = learning.signal_count or 0
                stale_note = ""
                if current - gen_count >= 5:
                    stale_note = f"  ← {current - gen_count} new signals since gen, regen on next backfill"
                print(f"Signals at gen:  {gen_count}{stale_note}")
        else:
            print("=== Violation description ===")
            print("(none — backfill hasn't generated one yet, or no signals to ground on)")
        print()

        if learning.violation_embedding:
            vec = learning.violation_embedding
            print("=== Embedding ===")
            print(f"Dimensions:    {len(vec)}")
            preview = [round(v, 4) for v in vec[:8]]
            print(f"First 8 vals:  {preview}")
            try:
                norm = math.sqrt(sum(v * v for v in vec))
                print(f"L2 norm:       {norm:.4f}  (≈1.0 means normalized — good)")
            except Exception:
                pass
        else:
            print("=== Embedding ===")
            print("(none — description present but not embedded, or no description to embed)")


def cmd_rules_list(args):
    """List active learnings with their description status. Helps
    find rule IDs to inspect with `maiko rule-show <id>`."""
    from planet_maiko.app import create_app
    from planet_maiko.models.learning import Learning

    app = create_app(start_scheduler=False)
    with app.app_context():
        query = Learning.query
        if args.status:
            query = query.filter_by(status=args.status)
        if args.repo:
            query = query.filter_by(scope_repo=args.repo)
        if args.category:
            query = query.filter_by(category=args.category)
        if args.missing_description:
            query = query.filter(Learning.violation_description.is_(None))
        learnings = query.order_by(Learning.signal_count.desc().nullslast()).all()

        if not learnings:
            print("(no learnings match)")
            return

        print(f"{'ID':>5}  {'CAT':<14}  {'SIG':>3}  {'DESC':<5}  RULE")
        print("-" * 80)
        for l in learnings:
            has_desc = "✓" if l.violation_description else "—"
            cat = (l.category or "")[:14]
            rule = (l.rule or "").replace("\n", " ")
            if len(rule) > 60:
                rule = rule[:60] + "…"
            sig = l.signal_count or 0
            print(f"{l.id:>5}  {cat:<14}  {sig:>3}  {has_desc:<5}  {rule}")
        print()
        print(f"Total: {len(learnings)} learnings")


def _detect_job_id_from_env():
    """If we're running inside a Maiko agent worktree, the kickoff
    wrote .maiko-env.json with the job_id. Read it transparently so
    agents get rules-considered tracking without remembering a flag.

    Falls back to the older `task_id` field for worktrees written
    before the rename.
    """
    import json as _json
    env_path = os.path.join(os.getcwd(), ".maiko-env.json")
    if not os.path.exists(env_path):
        return None
    try:
        with open(env_path, encoding="utf-8") as f:
            env = _json.load(f)
        return (env.get("job_id") or env.get("task_id") or "").strip() or None
    except Exception:
        return None


# Back-compat alias for older callers.
_detect_task_id_from_env = _detect_job_id_from_env


def cmd_rules_relevant(args):
    """Print the team's rules most relevant to the supplied input.

    Two modes:
      - File / stdin: a code diff, decomposed via Haiku before
        retrieval. Right for testing the end-to-end pipeline or
        for callers with no opinion on what the diff is doing.
      - One or more `--query` flags: free-text descriptions used
        directly, no Haiku step. Right for agents that have full
        repo context and have decomposed the change themselves.

    When run inside an agent worktree (.maiko-env.json present) or
    with --task-id, the retrieval is also persisted to
    task.extra.rules_considered so the diff page can show the user
    which team rules the agent had in mind during this work.
    """
    from datetime import datetime, timezone
    from planet_maiko.app import create_app
    from planet_maiko.brain.learning.rule_retrieval import find_relevant_rules
    from planet_maiko.brain.learning.embeddings import embedding_model_name
    from planet_maiko.models.learning import Learning

    queries = [q for q in (args.query or []) if q and q.strip()]
    diff = ""

    if not queries:
        if args.file:
            with open(args.file) as f:
                diff = f.read()
        else:
            if sys.stdin.isatty():
                print(
                    "Paste the diff or code (Ctrl+D when done), "
                    "or pass one or more --query \"...\" flags:",
                    file=sys.stderr,
                )
            diff = sys.stdin.read()

        if not diff.strip():
            print("Error: no input. Pass a file, pipe a diff, or use --query.",
                  file=sys.stderr)
            sys.exit(1)

    task_id = (args.task_id or "").strip() or _detect_task_id_from_env()

    app = create_app(start_scheduler=False)
    with app.app_context():
        if queries:
            matches = find_relevant_rules(
                queries=queries,
                repo=args.repo,
                k=args.k,
                min_similarity=args.min_similarity,
            )
        else:
            matches = find_relevant_rules(
                diff,
                repo=args.repo,
                k=args.k,
                min_similarity=args.min_similarity,
            )

        # Persist to task.extra.rules_considered so the user can see
        # what the agent had in mind on the diff/report page. Append-
        # only — every retrieval the agent runs adds a record. No-op
        # when no task_id (CLI used outside an agent worktree).
        if task_id and matches:
            from planet_maiko.database import db
            from planet_maiko.models.task import Task
            try:
                task = db.session.get(Task, task_id)
                if task is not None:
                    extra = dict(task.extra or {})
                    history = list(extra.get("rules_considered") or [])
                    history.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "queries": queries or ["(diff-decomposed)"],
                        "rules": [
                            {
                                "id": item["learning"].id,
                                "rule": item["learning"].rule,
                                "category": item["learning"].category,
                                "score": round(item["score"], 4),
                            }
                            for item in matches
                        ],
                    })
                    extra["rules_considered"] = history
                    task.extra = extra
                    db.session.commit()
            except Exception as e:
                # Never fail the CLI on persistence — the retrieval
                # output is what the agent actually needs. Log to
                # stderr so the user notices if it's broken.
                print(
                    f"(rules-considered persistence skipped: {e})",
                    file=sys.stderr,
                )
        rules_indexed = (
            Learning.query
            .filter_by(status="active")
            .filter(Learning.violation_embedding.isnot(None))
            .count()
        )
        rules_total = Learning.query.filter_by(status="active").count()

        print(f"\n=== Rule retrieval ===")
        print(f"Embedding model: {embedding_model_name() or '(none — backend unavailable)'}")
        print(f"Rules indexed: {rules_indexed} / {rules_total} active")
        if args.repo:
            print(f"Scope: {args.repo} (+ globals)")
        else:
            print("Scope: all rules")
        print()

        if not matches:
            print("(no matches above similarity threshold)")
            return

        for i, item in enumerate(matches, 1):
            l = item["learning"]
            print(f"[{i}] {l.rule}")
            print(f"     category: {l.category}   score: {item['score']:.3f}   signals: {l.signal_count or 0}")
            if l.violation_description:
                desc = l.violation_description
                # Wrap long descriptions for readability.
                if len(desc) > 200:
                    desc = desc[:200] + "…"
                print(f"     pattern: {desc}")
            print()

