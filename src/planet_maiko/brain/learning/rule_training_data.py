"""Rule-based training data generation.

For each active learning (rule), generates a balanced training dataset:
  1. Real signals — actual code examples from the team's history
  2. Synthetic violations — Claude generates code that breaks the rule
  3. Synthetic passes — Claude generates code that follows the rule

This produces focused, high-quality training data tied to specific rules
the team cares about, rather than noisy PR-scraping.

Usage:
    from planet_maiko.brain.learning.rule_training_data import generate_rule_dataset
    result = generate_rule_dataset(examples_per_rule=50)
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

EXAMPLES_PER_RULE = 50  # 25 violations + 25 passes by default

SYNTH_PROMPT = """You are generating training data for a code compliance model. The model needs to learn this rule:

**Rule:** {rule}
**Category:** {category}
{scope_info}
{repo_patterns_section}
{real_examples_section}

Generate {num_violations} VIOLATION examples and {num_passes} PASS examples. Each example should be a realistic code snippet (10-40 lines) that a developer might actually write.

VIOLATION examples should subtly break the rule — not cartoonishly bad code, but realistic mistakes.
PASS examples should follow the rule correctly — clean, idiomatic code.

Vary the examples: different function names, different contexts, different languages if applicable. Make them look like real code from real projects.

Respond with ONLY a JSON object:
{{
  "violations": [
    {{"code": "def foo():\\n    ...", "explanation": "brief explanation of what's wrong"}},
    ...
  ],
  "passes": [
    {{"code": "def bar():\\n    ...", "explanation": "brief explanation of why this is correct"}},
    ...
  ]
}}"""


def _safe_repo_name(repo):
    """Convert repo name to a filesystem-safe string."""
    if not repo:
        return ""
    return repo.replace("/", "--").replace("\\", "--")


def get_covered_rule_ids(output_dir=None, repo=None):
    """Return set of learning IDs that already have training data.

    Args:
        output_dir: directory to scan (default: data_dir/training-data)
        repo: if provided, only count rules from datasets matching this repo
              (filename starts with rules-{safe_repo}-)
    """
    from planet_maiko.paths import data_dir

    if output_dir is None:
        output_dir = os.path.join(data_dir(), "training-data")
    if not os.path.isdir(output_dir):
        return set()

    safe_repo = _safe_repo_name(repo) if repo else None

    covered = set()
    for fname in os.listdir(output_dir):
        if not fname.startswith("rules-"):
            continue
        # Partial files (`.jsonl.partial`) are from crashed/aborted runs.
        # We still count their rule_ids as covered so a resumed run skips
        # them instead of redoing the LLM work.
        if fname.endswith(".jsonl.partial"):
            stem = fname[len("rules-"):-len(".jsonl.partial")]
        elif fname.endswith(".jsonl"):
            stem = fname[len("rules-"):-len(".jsonl")]
        else:
            continue

        first = stem.split("-", 1)[0]
        is_global_file = first.isdigit()  # e.g. rules-20260408-024435.jsonl

        if safe_repo:
            # Include repo-specific files AND global files (global rules apply everywhere)
            is_repo_file = fname.startswith(f"rules-{safe_repo}-")
            if not is_repo_file and not is_global_file:
                continue
        else:
            # No repo filter — only look at global files
            if not is_global_file:
                continue

        fpath = os.path.join(output_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        pair = json.loads(line)
                        if "rule_id" in pair:
                            covered.add(pair["rule_id"])
        except Exception:
            continue
    return covered


def generate_rule_dataset(examples_per_rule=EXAMPLES_PER_RULE, output_dir=None,
                          rule_ids=None, repo=None, progress_cb=None,
                          max_workers=3):
    """Generate training data from active learnings.

    Args:
        examples_per_rule: total examples per rule (split ~50/50 violations/passes)
        output_dir: where to save JSONL
        rule_ids: specific learning IDs to process (None = all active)
        repo: if provided, filter to learnings scoped to this repo (or global)
              and prefix the output filename with the repo name
        progress_cb: optional callable for the async rule-gen endpoint to
            stream per-rule progress. Kwargs: total_rules, rules_processed,
            current_rule, pairs, errors.
        max_workers: concurrent LLM calls. Default 3. Higher risks
            saturating the API or spawning too many claude subprocesses.

    Pairs are written incrementally to `<path>.jsonl.partial` as each
    rule's LLM call returns, then the file is renamed to `<path>.jsonl`
    on success. If the run crashes, the partial file stays behind — the
    next run's get_covered_rule_ids() treats those learning_ids as
    covered so the LLM work isn't repeated.

    Returns:
        dict with {success, pairs, rules_processed, file_path}
    """
    from planet_maiko.paths import data_dir
    from planet_maiko.database import db
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.signal import Signal
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model
    from planet_maiko.brain.learning.llm_pool import run_parallel
    from planet_maiko.brain.learning.repo_patterns import get_repo_patterns
    from sqlalchemy import or_

    if output_dir is None:
        output_dir = os.path.join(data_dir(), "training-data")
    os.makedirs(output_dir, exist_ok=True)

    query = Learning.query.filter_by(status="active")
    if rule_ids:
        query = query.filter(Learning.id.in_(rule_ids))
    if repo:
        # Include rules scoped to this repo + rules that have been
        # promoted to global (observed across 3+ repos). Legacy rows
        # with scope_repo=NULL and is_global=False (pre-migration)
        # are also included as implicitly-global.
        query = query.filter(
            or_(
                Learning.scope_repo == repo,
                Learning.is_global == True,  # noqa: E712
                Learning.scope_repo.is_(None),
            )
        )
    learnings = query.all()

    if not learnings:
        return {"success": False, "error": "No active learnings found. Run backfill first."}

    logger.info(f"[rule-data] Processing {len(learnings)} active learnings")

    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        return {"success": False, "error": "LLM runtime not available."}

    model = resolve_model("synthetic_data")

    # Fetch the repo's code patterns once up-front — cached for 30 days
    # so this only actually runs the exploration agent on first use or
    # after TTL expiry. Returns None for global runs or when the repo
    # has no local checkout, which is fine — the prompt just skips the
    # patterns section.
    repo_patterns_md = get_repo_patterns(repo) if repo else None
    if repo_patterns_md:
        repo_patterns_section = (
            "## Repo code patterns\n\n"
            "Use these patterns to make generated code look like it came "
            "from this repo — same naming style, same internal libs, "
            "same testing idioms:\n\n"
            f"{repo_patterns_md}\n"
        )
    else:
        repo_patterns_section = ""

    if progress_cb:
        progress_cb(total_rules=len(learnings), rules_processed=0)

    # --- Phase 1: gather every DB read on the main thread and extract
    # to plain dicts. Workers receive only primitives — they never
    # touch ORM instances or db.session. ---
    jobs = []
    for learning in learnings:
        real_signals = Signal.query.filter_by(learning_id=learning.id).all()

        real_examples = []  # pure data, no ORM refs
        for s in real_signals:
            if s.examples:
                for ex in s.examples:
                    hunk = (ex.get("diff_hunk") or "").strip()
                    if not hunk:
                        continue
                    real_examples.append({
                        "path": ex.get("path") or s.file_path,
                        "diff_hunk": hunk,
                        "text": s.text,
                        "repo": s.repo,
                    })
            elif s.code_context:
                # Back-compat for rows created before examples existed.
                real_examples.append({
                    "path": s.file_path,
                    "diff_hunk": s.code_context,
                    "text": s.text,
                    "repo": s.repo,
                })

        real_pairs = []
        for ex in real_examples:
            context_parts = []
            if ex["path"]:
                context_parts.append(f"File: {ex['path']}")
            if ex["repo"]:
                context_parts.append(f"Repo: {ex['repo']}")
            context_parts.append(f"```\n{ex['diff_hunk']}\n```")
            real_pairs.append({
                "input": "\n".join(context_parts),
                "output": f"VIOLATION: [{learning.category}] {ex['text']}",
                "rule_id": learning.id,
                "rule": learning.rule,
                "category": learning.category,
                "repo": ex["repo"] or "",
                "source": "signal",
            })

        real_violation_count = len(real_examples)
        num_violations = max(0, (examples_per_rule // 2) - real_violation_count)
        num_passes = examples_per_rule // 2

        prompt = None
        if num_violations > 0 or num_passes > 0:
            real_examples_section = ""
            if real_examples:
                examples_text = ""
                for ex in real_examples[:3]:
                    label = f" ({ex['path']})" if ex["path"] else ""
                    examples_text += (
                        f"\n- Code{label}: {ex['diff_hunk'][:300]}\n"
                        f"  Feedback: {ex['text']}\n"
                    )
                real_examples_section = (
                    "Here are real examples of this rule being violated "
                    "in the team's codebase:\n"
                    f"{examples_text}\n"
                    "Use these as reference for the language, style, and "
                    "severity of violations. Generate examples in the "
                    "SAME language and framework as these real examples."
                )

            scope_info = ""
            if learning.scope_repo:
                scope_info += f"Repo: {learning.scope_repo}\n"

            language = learning.scope_language
            if not language:
                extensions = [os.path.splitext(s.file_path)[1] for s in real_signals if s.file_path]
                ext_map = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript/React",
                           ".jsx": "JavaScript/React", ".java": "Java", ".go": "Go", ".rb": "Ruby",
                           ".rs": "Rust", ".kt": "Kotlin", ".swift": "Swift", ".cs": "C#", ".cpp": "C++"}
                for ext in extensions:
                    if ext in ext_map:
                        language = ext_map[ext]
                        break

            if language:
                scope_info += f"Language: {language}\n"
                scope_info += f"Generate all code examples in {language}.\n"
            else:
                scope_info += "Infer the appropriate language from the real examples below, or use Python if no examples are available.\n"

            repos = list({s.repo for s in real_signals if s.repo})
            if repos:
                scope_info += f"Repos this rule applies to: {', '.join(repos)}\n"

            prompt = SYNTH_PROMPT.format(
                rule=learning.rule,
                category=learning.category,
                scope_info=scope_info,
                repo_patterns_section=repo_patterns_section,
                real_examples_section=real_examples_section,
                num_violations=num_violations,
                num_passes=num_passes,
            )

        jobs.append({
            "learning_id": learning.id,
            "rule": learning.rule,
            "rule_preview": learning.rule[:80],
            "category": learning.category,
            "real_pairs": real_pairs,
            "real_violation_count": real_violation_count,
            "prompt": prompt,
        })

    # Release the main-thread session — workers don't need it and
    # holding it open across the pool is pointless.
    db.session.close()

    # --- Phase 2: open partial file, fan LLM calls out through the
    # pool, write each rule's pairs as its future completes. ---
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if repo:
        safe_repo = _safe_repo_name(repo)
        filename = f"rules-{safe_repo}-{timestamp}.jsonl"
    else:
        filename = f"rules-{timestamp}.jsonl"
    output_path = os.path.join(output_dir, filename)
    partial_path = output_path + ".partial"

    state = {
        "pairs": 0,
        "violations": 0,
        "passes": 0,
        "rules_processed": 0,
        "errors": 0,
    }

    def runner(job):
        # Runs in a worker thread. Pure function of the job dict — no
        # ORM, no db.session, no Flask app state.
        if not job["prompt"]:
            # Rule already has enough real examples; nothing to synth.
            return {"success": True, "parsed": {"violations": [], "passes": []}}
        return runtime.send_json(job["prompt"], timeout=180, model=model)

    try:
        with open(partial_path, "w", encoding="utf-8") as out_file:
            def on_result(job, result, error):
                # Real pairs are verified code samples and don't depend
                # on the LLM — always write them, even if synth failed.
                for pair in job["real_pairs"]:
                    out_file.write(json.dumps(pair, ensure_ascii=False) + "\n")
                    state["pairs"] += 1
                    state["violations"] += 1

                parsed = None
                if not error and result and result.get("success"):
                    parsed = result.get("parsed")

                synth_violations = 0
                synth_passes = 0
                if parsed is not None:
                    for v in parsed.get("violations", []):
                        code = v.get("code", "")
                        explanation = v.get("explanation", "")
                        if not code:
                            continue
                        out_file.write(json.dumps({
                            "input": f"```\n{code}\n```",
                            "output": f"VIOLATION: [{job['category']}] {explanation}",
                            "rule_id": job["learning_id"],
                            "rule": job["rule"],
                            "category": job["category"],
                            "source": "synthetic",
                        }, ensure_ascii=False) + "\n")
                        synth_violations += 1

                    for p in parsed.get("passes", []):
                        code = p.get("code", "")
                        if not code:
                            continue
                        out_file.write(json.dumps({
                            "input": f"```\n{code}\n```",
                            "output": "PASS",
                            "rule_id": job["learning_id"],
                            "rule": job["rule"],
                            "category": job["category"],
                            "source": "synthetic",
                        }, ensure_ascii=False) + "\n")
                        synth_passes += 1

                    state["pairs"] += synth_violations + synth_passes
                    state["violations"] += synth_violations
                    state["passes"] += synth_passes
                    state["rules_processed"] += 1
                elif job["prompt"]:
                    state["errors"] += 1
                    logger.warning(
                        f"[rule-data] Rule #{job['learning_id']}: no synth "
                        f"parsed ({error or 'LLM returned no output'})"
                    )

                out_file.flush()

                logger.info(
                    f"[rule-data] Rule #{job['learning_id']}: "
                    f"{synth_violations} synthetic violations, "
                    f"{synth_passes} synthetic passes + "
                    f"{job['real_violation_count']} real signals"
                )
                if progress_cb:
                    progress_cb(
                        current_rule=f"#{job['learning_id']}: {job['rule_preview']}",
                        rules_processed=state["rules_processed"],
                        pairs=state["pairs"],
                        errors=state["errors"],
                    )

            run_parallel(jobs, runner, max_workers=max_workers,
                         on_result=on_result, log_prefix="rule-gen")
    except Exception:
        # Partial file stays behind so the next run picks up where we
        # left off — don't delete it on crash.
        logger.exception("[rule-data] Pool aborted; partial file preserved")
        raise

    if state["pairs"] == 0:
        # Nothing actually got written — remove the empty partial.
        try:
            os.remove(partial_path)
        except OSError:
            pass
        return {"success": False, "error": "No training pairs generated."}

    # Commit: rename partial → final. os.replace is atomic on both
    # POSIX and Windows within the same filesystem.
    os.replace(partial_path, output_path)
    logger.info(f"[rule-data] Wrote {state['pairs']} pairs to {output_path}")

    return {
        "success": True,
        "pairs": state["pairs"],
        "violations": state["violations"],
        "passes": state["passes"],
        "rules_processed": state["rules_processed"],
        "errors": state["errors"],
        "file_path": output_path,
    }
