"""Reporting helpers for the LoRA holdout-eval runner.

Pulls the markdown / JSON formatting code out of holdout.py so the
runner is small and the report shape lives with the cli command
that actually consumes it (cli/lora_cmds/review.py).

Public API:
    format_report(result, against=None)   markdown report
    to_json(result)                        machine-readable shape
    diff_against(current, prev_json_path)  delta between two runs
    category_breakdown(scores, use_adapter)
"""

import json
import re

# Re-export the regex used by category_breakdown — kept here so the
# helper file is self-contained.


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _pct(x):
    return "—" if x is None else f"{x:.0%}"


# ---------------------------------------------------------------------------
# Per-category breakdown
# ---------------------------------------------------------------------------

_CATEGORY_RE = re.compile(
    r"VIOLATION\s*:?\s*\[\s*([a-z_]+)\s*\]",
    re.IGNORECASE,
)


def _categories_in_output(raw):
    """Pull every `[category]` tag out of a model output. Returns a
    set so one file doesn't double-count the same category even if
    the model emitted two VIOLATION lines under the same tag."""
    return {m.group(1).lower() for m in _CATEGORY_RE.finditer(raw or "")}


def category_breakdown(scores, use_adapter=True):
    """For each category the model emitted, count:
      - total_flags: files where the model used that category
      - file_hits: flags that landed on human-flagged files
      - semantic_hits: flags the judge confirmed (if run in judge mode)

    We can't compute *recall* by category — human comments don't carry
    explicit categories, so there's no denominator for "how many
    null_safety issues did the human raise". Precision is honest,
    though: "when the model says null_safety, how often is it right?"
    """
    buckets = {}
    for s in scores:
        outputs = s.model_outputs_with if use_adapter else s.model_outputs_without
        flagged = s.model_flagged_with if use_adapter else s.model_flagged_without
        sem_hits = s.semantic_hits_with if use_adapter else s.semantic_hits_without
        for fp in flagged:
            cats = _categories_in_output(outputs.get(fp, ""))
            if not cats:
                cats = {"(uncategorized)"}
            for c in cats:
                b = buckets.setdefault(c, {"total_flags": 0, "file_hits": 0, "semantic_hits": 0})
                b["total_flags"] += 1
                if fp in s.human_files:
                    b["file_hits"] += 1
                if fp in sem_hits:
                    b["semantic_hits"] += 1
    return buckets


# ---------------------------------------------------------------------------
# Delta vs a previous JSON run
# ---------------------------------------------------------------------------

def diff_against(current, prev_json_path):
    """Compare current run to a previously-saved JSON result.

    Returns a dict shaped:

        {
          "prev_adapter": "...",
          "prev_generated_at": "...",
          "deltas": {
            "recall_loose_with": +0.12,
            "recall_strict_with": +0.05,
            ...
          },
          "regressions": ["https://...#42 (strict recall 80% -> 40%)"],
          "improvements": ["https://...#17 (strict recall 0% -> 60%)"],
        }

    regression / improvement are PR-level — tells you which specific
    PRs got better or worse between runs. Useful for "did the latest
    training round fix what I was trying to fix, or did it regress
    something else?"
    """
    with open(prev_json_path, encoding="utf-8") as f:
        prev = json.load(f)

    cur_agg = to_json(current)["aggregate"]
    prev_agg = prev.get("aggregate") or {}

    deltas = {}
    for k in ("recall_loose_with", "recall_strict_with", "precision_with",
              "recall_loose_without", "recall_strict_without", "precision_without"):
        a = cur_agg.get(k)
        b = prev_agg.get(k)
        if a is None or b is None:
            continue
        deltas[k] = a - b

    prev_by_url = {p["pr"]["url"]: p for p in prev.get("pr_scores") or []}
    regressions = []
    improvements = []
    scores = current.get("scores") or []
    for s in scores:
        p = prev_by_url.get(s.pr.url)
        if not p:
            continue
        # Prefer strict recall where available, fall back to loose.
        cur_strict = s.recall(with_adapter=True, strict=True)
        prev_strict = p.get("recall_strict_with")
        if cur_strict is not None and prev_strict is not None:
            if cur_strict < prev_strict - 0.0001:
                regressions.append(f"{s.pr.repo}#{s.pr.number}: strict recall {prev_strict:.0%} -> {cur_strict:.0%}")
            elif cur_strict > prev_strict + 0.0001:
                improvements.append(f"{s.pr.repo}#{s.pr.number}: strict recall {prev_strict:.0%} -> {cur_strict:.0%}")
            continue
        cur_loose = s.recall(with_adapter=True, strict=False)
        prev_loose = p.get("recall_loose_with")
        if cur_loose is not None and prev_loose is not None:
            if cur_loose < prev_loose - 0.0001:
                regressions.append(f"{s.pr.repo}#{s.pr.number}: loose recall {prev_loose:.0%} -> {cur_loose:.0%}")
            elif cur_loose > prev_loose + 0.0001:
                improvements.append(f"{s.pr.repo}#{s.pr.number}: loose recall {prev_loose:.0%} -> {cur_loose:.0%}")

    return {
        "prev_adapter": prev.get("adapter_path"),
        "prev_generated_at": prev.get("generated_at"),
        "deltas": deltas,
        "regressions": regressions,
        "improvements": improvements,
    }


def to_json(result):
    """Machine-readable dump of a run() result.

    Written alongside the markdown so future runs can diff against
    prior ones without re-parsing markdown. Sets become sorted lists,
    PRScore dataclasses get flattened into dicts.
    """
    scores = []
    for s in result.get("scores") or []:
        scores.append({
            "pr": {
                "url": s.pr.url,
                "repo": s.pr.repo,
                "number": s.pr.number,
                "notes": s.pr.notes,
            },
            "human_files": sorted(s.human_files),
            "model_flagged_with": sorted(s.model_flagged_with),
            "model_flagged_without": sorted(s.model_flagged_without),
            "semantic_hits_with": sorted(s.semantic_hits_with),
            "semantic_hits_without": sorted(s.semantic_hits_without),
            "file_count": s.file_count,
            "errors": s.errors,
            "model_outputs_with": s.model_outputs_with,
            "model_outputs_without": s.model_outputs_without,
            "recall_loose_with": s.recall(with_adapter=True, strict=False),
            "recall_loose_without": s.recall(with_adapter=False, strict=False),
            "recall_strict_with": s.recall(with_adapter=True, strict=True),
            "recall_strict_without": s.recall(with_adapter=False, strict=True),
            "precision_with": s.precision(True),
            "precision_without": s.precision(False),
        })
    # Micro-averaged aggregates — same as what the markdown shows but
    # spelled out here so comparison scripts don't have to recompute.
    tot_h = sum(len(s.human_files) for s in result.get("scores") or [])
    tot_hits_w = sum(len(s.hits_with()) for s in result.get("scores") or [])
    tot_sem_w = sum(len(s.semantic_hits_with) for s in result.get("scores") or [])
    tot_flagged_w = sum(len(s.model_flagged_with) for s in result.get("scores") or [])
    agg = {
        "recall_loose_with": (tot_hits_w / tot_h) if tot_h else None,
        "recall_strict_with": (tot_sem_w / tot_h) if tot_h else None,
        "precision_with": (tot_hits_w / tot_flagged_w) if tot_flagged_w else None,
        "human_files_total": tot_h,
        "model_flagged_total": tot_flagged_w,
    }
    if result.get("compare_baseline"):
        tot_hits_wo = sum(len(s.hits_without()) for s in result.get("scores") or [])
        tot_sem_wo = sum(len(s.semantic_hits_without) for s in result.get("scores") or [])
        tot_flagged_wo = sum(len(s.model_flagged_without) for s in result.get("scores") or [])
        agg.update({
            "recall_loose_without": (tot_hits_wo / tot_h) if tot_h else None,
            "recall_strict_without": (tot_sem_wo / tot_h) if tot_h else None,
            "precision_without": (tot_hits_wo / tot_flagged_wo) if tot_flagged_wo else None,
            "model_flagged_baseline_total": tot_flagged_wo,
        })
    return {
        "fixture_name": result.get("fixture_name"),
        "fixture_description": result.get("fixture_description"),
        "adapter_path": result.get("adapter_path"),
        "compare_baseline": result.get("compare_baseline"),
        "match_mode": result.get("match_mode"),
        "ground_truth_cached_at": result.get("ground_truth_cached_at"),
        "generated_at": result.get("generated_at"),
        "aggregate": agg,
        "pr_scores": scores,
        "judge_detail": result.get("judge_detail") or {},
    }


def format_report(result, against=None):
    """Turn a run() result into a markdown report.

    `against` is an optional diff_against() dict — when provided, a
    'Vs previous run' section is appended with aggregate deltas and
    per-PR regressions/improvements.
    """
    if "error" in result:
        return f"# Holdout eval failed\n\n{result['error']}\n"

    scores = result["scores"]
    if not scores:
        return "# Holdout eval\n\nNo PRs produced scoreable output.\n"

    compare = result["compare_baseline"]
    judge_mode = result.get("match_mode") == "judge"

    # Aggregates (micro-averaged over files, not over PRs — one PR with
    # 20 commented files shouldn't get the same weight as a PR with 1).
    tot_human = sum(len(s.human_files) for s in scores)
    tot_hits_with = sum(len(s.hits_with()) for s in scores)
    tot_flagged_with = sum(len(s.model_flagged_with) for s in scores)
    recall_with = (tot_hits_with / tot_human) if tot_human else None
    prec_with = (tot_hits_with / tot_flagged_with) if tot_flagged_with else None
    tot_sem_with = sum(len(s.semantic_hits_with) for s in scores)
    strict_recall_with = (tot_sem_with / tot_human) if tot_human and judge_mode else None

    if compare:
        tot_hits_wo = sum(len(s.hits_without()) for s in scores)
        tot_flagged_wo = sum(len(s.model_flagged_without) for s in scores)
        recall_wo = (tot_hits_wo / tot_human) if tot_human else None
        prec_wo = (tot_hits_wo / tot_flagged_wo) if tot_flagged_wo else None
        tot_sem_wo = sum(len(s.semantic_hits_without) for s in scores)
        strict_recall_wo = (tot_sem_wo / tot_human) if tot_human and judge_mode else None

    lines = [
        f"# Holdout eval — {result['fixture_name']}",
        "",
    ]
    if result.get("fixture_description"):
        lines.append(result["fixture_description"])
        lines.append("")
    lines.append(f"Adapter: `{result['adapter_path'] or '(base model — no adapter)'}`  ")
    lines.append(f"Generated: {result['generated_at']}  ")
    lines.append(f"Match mode: **{result.get('match_mode', 'file')}**  ")
    lines.append(f"PRs scored: {len(scores)}  ")
    cached_at = result.get("ground_truth_cached_at")
    if cached_at:
        lines.append(f"Ground truth snapshot: {cached_at}")
    lines.append("")
    if judge_mode:
        lines.append("*Strict recall counts a human-flagged file as a hit only when the judge "
                     "LLM confirms the model's output addresses the same concern. Loose recall "
                     "counts flagging the same file at all.*")
        lines.append("")

    lines.append("## Aggregate")
    lines.append("")
    if compare:
        if judge_mode:
            lines.append("| Mode | Strict recall | Loose recall | Precision | Flagged | Strict hits |")
            lines.append("|---|---|---|---|---|---|")
            lines.append(
                f"| With adapter | {_pct(strict_recall_with)} | {_pct(recall_with)} | "
                f"{_pct(prec_with)} | {tot_flagged_with} | {tot_sem_with} |"
            )
            lines.append(
                f"| Baseline (no adapter) | {_pct(strict_recall_wo)} | {_pct(recall_wo)} | "
                f"{_pct(prec_wo)} | {tot_flagged_wo} | {tot_sem_wo} |"
            )
            if strict_recall_with is not None and strict_recall_wo is not None:
                delta = strict_recall_with - strict_recall_wo
                sign = "+" if delta >= 0 else ""
                lines.append("")
                lines.append(f"**Strict-recall delta from training: {sign}{delta:.0%}** (higher is better)")
        else:
            lines.append("| Mode | Recall | Precision | Flagged files | Hits |")
            lines.append("|---|---|---|---|---|")
            lines.append(
                f"| With adapter | {_pct(recall_with)} | {_pct(prec_with)} | "
                f"{tot_flagged_with} | {tot_hits_with} |"
            )
            lines.append(
                f"| Baseline (no adapter) | {_pct(recall_wo)} | {_pct(prec_wo)} | "
                f"{tot_flagged_wo} | {tot_hits_wo} |"
            )
            if recall_with is not None and recall_wo is not None:
                delta = recall_with - recall_wo
                sign = "+" if delta >= 0 else ""
                lines.append("")
                lines.append(f"**Recall delta from training: {sign}{delta:.0%}** (higher is better)")
    else:
        if judge_mode:
            lines.append(f"- Strict recall: {_pct(strict_recall_with)}")
            lines.append(f"- Loose recall: {_pct(recall_with)}")
        else:
            lines.append(f"- Recall: {_pct(recall_with)}")
        lines.append(f"- Precision: {_pct(prec_with)}")
        lines.append(f"- Human-flagged files across fixture: {tot_human}")
        lines.append(f"- Model-flagged files: {tot_flagged_with}")

    lines.append("")
    lines.append("## Per PR")
    lines.append("")
    judge_detail = result.get("judge_detail") or {}
    for s in scores:
        lines.append(f"### {s.pr.repo}#{s.pr.number}")
        lines.append("")
        lines.append(f"- URL: {s.pr.url}")
        if s.pr.notes:
            lines.append(f"- Notes: {s.pr.notes}")
        lines.append(f"- Human-flagged files: {len(s.human_files)}")
        lines.append(f"- Model flagged: {len(s.model_flagged_with)} / {s.file_count} reviewed")
        if compare:
            lines.append(f"- Baseline flagged: {len(s.model_flagged_without)} / {s.file_count}")
        lines.append(f"- Loose recall: {_pct(s.recall(with_adapter=True))}")
        if judge_mode:
            lines.append(f"- Strict recall: {_pct(s.recall(with_adapter=True, strict=True))}")
        if compare:
            lines.append(f"- Baseline loose recall: {_pct(s.recall(with_adapter=False))}")
            if judge_mode:
                lines.append(f"- Baseline strict recall: {_pct(s.recall(with_adapter=False, strict=True))}")
        if s.errors:
            lines.append(f"- Inference errors: {s.errors}")

        hits = sorted(s.hits_with())
        misses = sorted(s.human_files - s.model_flagged_with)
        extras = sorted(s.model_flagged_with - s.human_files)

        def _short(text, n=200):
            t = (text or "").replace("\n", " ").strip()
            return t[:n] + ("…" if len(t) > n else "")

        def _human_comment_for(fp):
            comments = s.human_comments_by_file.get(fp) or []
            if not comments:
                return ""
            # Show the first comment; surface a count if there were more.
            first = comments[0]
            extra = f" (+{len(comments)-1} more)" if len(comments) > 1 else ""
            return _short(first) + extra

        if hits:
            lines.append("- Hits (model and humans both flagged):")
            for hpath in hits:
                model = _short(s.model_outputs_with.get(hpath))
                human = _human_comment_for(hpath)
                lines.append(f"    - `{hpath}`")
                if human:
                    lines.append(f"      - human: _{human}_")
                if model:
                    lines.append(f"      - model: _{model}_")
        if misses:
            lines.append("- Missed (humans flagged, model didn't):")
            for mpath in misses:
                model = _short(s.model_outputs_with.get(mpath))
                human = _human_comment_for(mpath)
                lines.append(f"    - `{mpath}`")
                if human:
                    lines.append(f"      - human: _{human}_")
                lines.append(f"      - model: _{model or '(not reviewed — no diff)'}_")
        if extras:
            lines.append("- Extras (model flagged, humans didn't):")
            for epath in extras:
                model = _short(s.model_outputs_with.get(epath))
                lines.append(f"    - `{epath}`")
                if model:
                    lines.append(f"      - model: _{model}_")

        if judge_mode:
            detail = judge_detail.get(s.pr.url, {}).get("with") or {}
            if detail:
                lines.append("")
                lines.append("**Judge notes (with adapter):**")
                for fp, info in sorted(detail.items()):
                    for m in info.get("matches", []):
                        mark = "[match]" if m["match"] else "[miss] "
                        why = m.get("why", "")
                        lines.append(f"- {mark} `{fp}` — {why}")
        lines.append("")

    # Per-category breakdown — tells you which rule types the training
    # covers well and which are weak. Precision only (no recall
    # denominator available — humans don't tag their comments).
    cats = category_breakdown(scores, use_adapter=True)
    if cats:
        lines.append("## Category precision (with adapter)")
        lines.append("")
        lines.append("| Category | Flags | File-level hits | Judge hits | File precision | Strict precision |")
        lines.append("|---|---|---|---|---|---|")
        for cat in sorted(cats.keys()):
            b = cats[cat]
            flags = b["total_flags"]
            fp_ = (b["file_hits"] / flags) if flags else None
            sp_ = (b["semantic_hits"] / flags) if flags and judge_mode else None
            lines.append(f"| `{cat}` | {flags} | {b['file_hits']} | {b['semantic_hits']} | {_pct(fp_)} | {_pct(sp_)} |")
        lines.append("")

    if against:
        lines.append("## Vs previous run")
        lines.append("")
        if against.get("prev_adapter"):
            lines.append(f"Previous adapter: `{against['prev_adapter']}`  ")
        if against.get("prev_generated_at"):
            lines.append(f"Previous run: {against['prev_generated_at']}")
        lines.append("")
        deltas = against.get("deltas") or {}
        if deltas:
            lines.append("| Metric | Delta |")
            lines.append("|---|---|")
            for k, v in deltas.items():
                sign = "+" if v >= 0 else ""
                lines.append(f"| {k} | {sign}{v:.0%} |")
            lines.append("")
        if against.get("improvements"):
            lines.append("**Improvements:**")
            for e in against["improvements"]:
                lines.append(f"- {e}")
            lines.append("")
        if against.get("regressions"):
            lines.append("**Regressions:**")
            for e in against["regressions"]:
                lines.append(f"- {e}")
            lines.append("")
        if not against.get("improvements") and not against.get("regressions"):
            lines.append("_No per-PR recall changes from the previous run._")
            lines.append("")

    return "\n".join(lines)
