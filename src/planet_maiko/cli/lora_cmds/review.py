"""Review / eval subcommands for the maiko LoRA CLI."""

import os
import re
import subprocess
import sys


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
    from planet_maiko.app import create_app

    eval_set = "train" if getattr(args, "on_training", False) else "holdout"

    # App context so evaluate_adapter can persist the row into the
    # adapter_evals table — /lora/adapters reads from that to surface
    # eval_score and trend.
    app = create_app(start_scheduler=False)
    with app.app_context():
        print(f"Evaluating adapter on {eval_set} set...")
        result = evaluate_adapter(
            adapter_path=args.adapter,
            repo=args.repo,
            holdout_fraction=args.holdout,
            eval_set=eval_set,
        )

    if not result.get("success"):
        print(f"Error: {result.get('error')}")
        return

    label = "Training set" if eval_set == "train" else "Holdout set"
    print(f"\n=== LoRA Evaluation ({label}) ===")
    print(f"Adapter: {result['adapter_path']}")
    print(f"Test examples: {result['test_count']}")
    print(f"Precision: {result['precision']:.1%}")
    print(f"Recall:    {result['recall']:.1%}")
    print(f"F1:        {result['f1']:.1%}")

    if eval_set == "train":
        print(
            "\nThis is the training set — F1 here should be high. "
            "Compare with `maiko eval` (holdout): a big gap is overfit."
        )

    if args.per_category and result.get("per_category"):
        print(f"\nPer-category breakdown:")
        for cat, metrics in sorted(result["per_category"].items()):
            print(f"  {cat:20s}  P={metrics['precision']:.0%}  R={metrics['recall']:.0%}  F1={metrics['f1']:.0%}  n={metrics['count']}")


def cmd_review_rag(args):
    """Run the full RAG-backed review pipeline on a diff/file:
    retrieve top-K rules → Claude reviews against them → print the
    review. Useful for testing the end-to-end flow."""
    from planet_maiko.app import create_app
    from planet_maiko.brain.learning.rag_review import review_with_rag

    if args.file:
        with open(args.file) as f:
            diff = f.read()
    else:
        if sys.stdin.isatty():
            print("Paste the diff or code (Ctrl+D when done):", file=sys.stderr)
        diff = sys.stdin.read()

    if not diff.strip():
        print("Error: no input.", file=sys.stderr)
        sys.exit(1)

    app = create_app(start_scheduler=False)
    with app.app_context():
        result = review_with_rag(
            diff,
            repo=args.repo,
            k=args.k,
            min_similarity=args.min_similarity,
        )

    if not result.get("success"):
        print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)

    rules = result.get("rules") or []
    print(f"=== Retrieved {len(rules)} rules for review ===")
    for r in rules:
        print(f"  [{r['category']}] {r['rule']}  (score: {r['score']:.3f})")
    print()
    print("=== Review ===")
    print(result["review"])


def cmd_review(args):
    """Review code using a trained LoRA adapter. For multi-hunk diffs,
    automatically chunks per hunk to match the LoRA's training
    distribution (each rules-*.jsonl pair was a single small chunk)."""
    from planet_maiko.brain.learning.trainer import review_diff

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

    result = review_diff(
        diff_text=code,
        repo=args.repo,
        file_path=file_path,
    )

    if not result.get("success"):
        print(f"Error: {result.get('error')}")
        return

    n = result.get("hunks_reviewed", 0)
    flagged = result.get("hunks_flagged", 0)
    if n > 1:
        print(f"\n=== Reviewed {n} hunks ({flagged} flagged) ===\n")

    violations = result.get("violations") or []
    if violations:
        for v in violations:
            print(v["raw"])
        return

    print("PASS")


from planet_maiko.utils import parse_pr_url as _parse_pr_url  # noqa: E402


def _review_pr(args):
    """Review each file in a GitHub PR individually through the LoRA model.
    Each per-file diff is further chunked per-hunk so the LoRA sees inputs
    matching its training distribution."""
    from planet_maiko.brain.learning.trainer import review_diff

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
        r = review_diff(diff_text=fd, adapter_path=None, repo=repo, file_path=fp)

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

