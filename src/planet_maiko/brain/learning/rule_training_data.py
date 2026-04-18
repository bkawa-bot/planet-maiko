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
        if not (fname.startswith("rules-") and fname.endswith(".jsonl")):
            continue

        stem = fname[len("rules-"):-len(".jsonl")]
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


def generate_rule_dataset(examples_per_rule=EXAMPLES_PER_RULE, output_dir=None, rule_ids=None, repo=None, progress_cb=None):
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

    Returns:
        dict with {success, pairs, rules_processed, file_path}
    """
    from planet_maiko.paths import data_dir
    from planet_maiko.database import db
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.signal import Signal
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model
    from sqlalchemy import or_

    if output_dir is None:
        output_dir = os.path.join(data_dir(), "training-data")
    os.makedirs(output_dir, exist_ok=True)

    # Get active learnings
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

    if progress_cb:
        progress_cb(total_rules=len(learnings), rules_processed=0)

    all_pairs = []
    rules_processed = 0
    errors = 0

    for learning in learnings:
        logger.info(f"[rule-data] Rule #{learning.id}: {learning.rule[:60]}...")
        if progress_cb:
            progress_cb(
                current_rule=f"#{learning.id}: {learning.rule[:80]}",
                rules_processed=rules_processed,
                pairs=len(all_pairs),
                errors=errors,
            )

        # Step 1: Pull real signals for this learning, flatten to one row
        # per (signal, example) so each distinct code snippet becomes its
        # own training pair. Signals without any code context are skipped.
        real_signals = Signal.query.filter_by(learning_id=learning.id).all()
        real_examples = []  # list of {signal, path, diff_hunk}
        for s in real_signals:
            if s.examples:
                for ex in s.examples:
                    hunk = (ex.get("diff_hunk") or "").strip()
                    if not hunk:
                        continue
                    real_examples.append({
                        "signal": s,
                        "path": ex.get("path") or s.file_path,
                        "diff_hunk": hunk,
                    })
            elif s.code_context:
                # Back-compat for rows created before examples existed.
                real_examples.append({
                    "signal": s,
                    "path": s.file_path,
                    "diff_hunk": s.code_context,
                })

        # Add one training pair per example.
        for ex in real_examples:
            s = ex["signal"]
            context_parts = []
            if ex["path"]:
                context_parts.append(f"File: {ex['path']}")
            if s.repo:
                context_parts.append(f"Repo: {s.repo}")
            context_parts.append(f"```\n{ex['diff_hunk']}\n```")

            all_pairs.append({
                "input": "\n".join(context_parts),
                "output": f"VIOLATION: [{learning.category}] {s.text}",
                "rule_id": learning.id,
                "rule": learning.rule,
                "category": learning.category,
                "repo": s.repo or "",
                "source": "signal",
            })

        # Step 2: Calculate how many synthetic examples we need
        real_violation_count = len(real_examples)
        num_violations = max(0, (examples_per_rule // 2) - real_violation_count)
        num_passes = examples_per_rule // 2

        if num_violations == 0 and num_passes == 0:
            rules_processed += 1
            continue

        # Step 3: Build prompt with real examples and inferred context
        real_examples_section = ""
        if real_examples:
            examples_text = ""
            for ex in real_examples[:3]:  # Show up to 3 real examples
                s = ex["signal"]
                label = f" ({ex['path']})" if ex["path"] else ""
                examples_text += f"\n- Code{label}: {ex['diff_hunk'][:300]}\n  Feedback: {s.text}\n"
            real_examples_section = f"Here are real examples of this rule being violated in the team's codebase:\n{examples_text}\nUse these as reference for the language, style, and severity of violations. Generate examples in the SAME language and framework as these real examples."

        # Build scope info — explicit fields + inferred from signals
        scope_info = ""
        if learning.scope_repo:
            scope_info += f"Repo: {learning.scope_repo}\n"

        # Infer language from scope or signal file paths
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

        # Include repo names from signals for framework context
        repos = list({s.repo for s in real_signals if s.repo})
        if repos:
            scope_info += f"Repos this rule applies to: {', '.join(repos)}\n"

        # Release DB before LLM call
        db.session.close()

        prompt = SYNTH_PROMPT.format(
            rule=learning.rule,
            category=learning.category,
            scope_info=scope_info,
            real_examples_section=real_examples_section,
            num_violations=num_violations,
            num_passes=num_passes,
        )

        result = runtime.send_json(prompt, timeout=180, model=resolve_model("synthetic_data"))

        if not result.get("success") or not result.get("parsed"):
            logger.warning(f"[rule-data] Failed for rule #{learning.id}: {result.get('error', 'no output')}")
            errors += 1
            continue

        parsed = result["parsed"]

        # Add synthetic violations
        for v in parsed.get("violations", []):
            code = v.get("code", "")
            explanation = v.get("explanation", "")
            if not code:
                continue
            all_pairs.append({
                "input": f"```\n{code}\n```",
                "output": f"VIOLATION: [{learning.category}] {explanation}",
                "rule_id": learning.id,
                "rule": learning.rule,
                "category": learning.category,
                "source": "synthetic",
            })

        # Add synthetic passes
        for p in parsed.get("passes", []):
            code = p.get("code", "")
            if not code:
                continue
            all_pairs.append({
                "input": f"```\n{code}\n```",
                "output": "PASS",
                "rule_id": learning.id,
                "rule": learning.rule,
                "category": learning.category,
                "source": "synthetic",
            })

        rules_processed += 1
        logger.info(f"[rule-data] Rule #{learning.id}: {len(parsed.get('violations', []))} synthetic violations, {len(parsed.get('passes', []))} synthetic passes + {real_violation_count} real signals")
        if progress_cb:
            progress_cb(
                rules_processed=rules_processed,
                pairs=len(all_pairs),
                errors=errors,
            )

    if not all_pairs:
        return {"success": False, "error": "No training pairs generated."}

    # Write dataset (include repo prefix when scoped)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if repo:
        safe_repo = _safe_repo_name(repo)
        filename = f"rules-{safe_repo}-{timestamp}.jsonl"
    else:
        filename = f"rules-{timestamp}.jsonl"
    output_path = os.path.join(output_dir, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    violations = sum(1 for p in all_pairs if not p["output"].startswith("PASS"))
    passes = sum(1 for p in all_pairs if p["output"].startswith("PASS"))

    logger.info(f"[rule-data] Wrote {len(all_pairs)} pairs to {output_path}")

    return {
        "success": True,
        "pairs": len(all_pairs),
        "violations": violations,
        "passes": passes,
        "rules_processed": rules_processed,
        "errors": errors,
        "file_path": output_path,
    }
