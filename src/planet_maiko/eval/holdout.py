"""PR-level holdout evaluation for trained LoRA adapters.

Answers "does the trained model flag the same things a human reviewer
did?" on a locked set of real PRs.

The per-PR flow:

  1. Pull the PR's diff via `gh pr diff` and split it per-file.
  2. Pull the human review comments (inline + review-body) and group
     them by file. These are the ground truth.
  3. Run each file's diff through `review_code(adapter_path=...)`. The
     model emits "PASS" or "VIOLATION: ..." — we collect the files it
     flagged.
  4. Score at the file level: a PR file is a "hit" if humans commented
     on it AND the model flagged it; a "miss" if humans commented but
     the model said PASS; an "extra" if the model flagged a file with
     no human comments.

Recall (hits / human-flagged files) is the headline — "of the issues
humans cared about, how many did the model surface?". Precision is
reported too but takes a back seat: the model flagging something
humans didn't isn't necessarily wrong (they might have missed it),
and punishing the model for being conservative would push training in
the wrong direction.

Baseline mode runs the same PRs without the adapter (base model only)
so the report can say "adapter adds +12% recall, -3% precision" —
answers "is training actually helping?" rather than just "how does
the trained model score?".
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from planet_maiko.brain.learning.training_data import (
    _get_review_comments,
    _get_review_bodies,
    _split_diff_by_file,
    _is_approval_comment,
)

logger = logging.getLogger(__name__)

_SKIP_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".lock",
    ".css", ".svg", ".png", ".jpg", ".gif", ".xml",
}


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

@dataclass
class HoldoutPR:
    url: str
    repo: str
    number: int
    notes: str = ""


def _parse_pr_url(url):
    m = re.match(r"https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)", url)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"([^/]+/[^/]+)#(\d+)", url)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


def load_fixture(fixture_path):
    """Parse a fixture file into HoldoutPR records.

    Fixture shape:

        {
          "name": "pr-review-v1",
          "description": "...",
          "prs": [
            {"url": "https://...", "notes": "..."},
            ...
          ]
        }
    """
    with open(fixture_path, encoding="utf-8") as f:
        data = json.load(f)
    prs = []
    for entry in data.get("prs") or []:
        url = (entry.get("url") or "").strip()
        if not url:
            continue
        repo, number = _parse_pr_url(url)
        if not repo or not number:
            logger.warning(f"[eval] Skipping unparseable PR: {url!r}")
            continue
        prs.append(HoldoutPR(url=url, repo=repo, number=number, notes=entry.get("notes", "")))
    return {
        "name": data.get("name") or os.path.basename(fixture_path),
        "description": data.get("description", ""),
        "prs": prs,
    }


def _cache_path_for(fixture_path):
    """Sibling file: `x.json` → `x.ground-truth.json`.

    Cache is a snapshot of the per-PR `{file_path: [comment, ...]}`
    dicts taken at first-ever run. Stable across subsequent runs so
    an eyelash edit on a PR comment doesn't shift the recall numbers
    between training cycles.
    """
    base, ext = os.path.splitext(fixture_path)
    return f"{base}.ground-truth.json"


def _load_ground_truth_cache(fixture_path):
    cache_path = _cache_path_for(fixture_path)
    if not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[eval] Could not read ground-truth cache {cache_path}: {e}")
        return None


def _save_ground_truth_cache(fixture_path, ground_truth_by_url):
    cache_path = _cache_path_for(fixture_path)
    payload = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "by_url": ground_truth_by_url,
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------

def fetch_ground_truth(pr: HoldoutPR):
    """Fetch human review comments for a PR, grouped by file path.

    Returns `{file_path: [comment_text, ...]}`. Review bodies (the
    overall review comment with approve/request changes) are attached
    to every file in the PR's diff — there's no path info on a review
    body, but humans write them when they have concerns about the PR
    as a whole, so counting them per-file is the honest default.
    """
    inline = _get_review_comments(pr.repo, pr.number) or []
    by_file = {}
    for c in inline:
        body = (c.get("body") or "").strip()
        path = c.get("path") or ""
        if not path or not body or _is_approval_comment(body) or len(body) < 15:
            continue
        by_file.setdefault(path, []).append(body)

    # Review-level bodies don't carry file paths, so record them under
    # a sentinel key. The scorer counts them as "PR-level concerns"
    # that match any file flag.
    review_bodies = _get_review_bodies(pr.repo, pr.number) or []
    if review_bodies:
        by_file.setdefault("__review_body__", []).extend(review_bodies)

    return by_file


def fetch_pr_files(pr: HoldoutPR):
    """Fetch per-file diffs for a PR. Returns `[(file_path, diff), ...]`
    filtered to files the model might reasonably review (no .md, .json,
    binaries)."""
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr.number), "--repo", pr.repo],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        logger.warning(f"[eval] gh pr diff failed for {pr.url}: {e}")
        return []
    if result.returncode != 0:
        logger.warning(f"[eval] gh pr diff non-zero for {pr.url}: {result.stderr.strip()}")
        return []

    files = []
    for file_path, hunk in _split_diff_by_file(result.stdout):
        ext = os.path.splitext(file_path)[1].lower()
        if ext in _SKIP_EXTENSIONS:
            continue
        if len(hunk) < 30:
            continue
        files.append((file_path, hunk))
    return files


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def _clean_mlx_output(raw):
    """Strip mlx_lm's chatter lines so we see only the model verdict.

    The MLX inference wrapper prints boilerplate like "Peak memory:"
    and "==========" around the real output; they trip up the scorer
    if left in. Same cleanup `cmd_review` in lora_cmds.py does.
    """
    out_lines = []
    for line in (raw or "").split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith(("Calling `python", "Prompt:", "Generation:", "Peak memory:")):
            continue
        if s == "==========":
            continue
        out_lines.append(s)
    return "\n".join(out_lines)


def review_pr_files(files, adapter_path):
    """Run every file diff through the model. Returns a dict:

        {file_path: {"verdict": "PASS"|"FLAG", "raw": "<model output>"}}

    adapter_path=None runs the base model for baseline comparison.
    """
    from planet_maiko.brain.learning.trainer import review_code

    out = {}
    for file_path, hunk in files:
        r = review_code(code=hunk, adapter_path=adapter_path, file_path=file_path)
        if not r.get("success"):
            out[file_path] = {"verdict": "ERROR", "raw": r.get("error", "")}
            continue
        cleaned = _clean_mlx_output(r.get("output", ""))
        verdict = "PASS" if cleaned.upper().startswith("PASS") else "FLAG"
        out[file_path] = {"verdict": verdict, "raw": cleaned}
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class PRScore:
    pr: HoldoutPR
    human_files: set = field(default_factory=set)
    model_flagged_with: set = field(default_factory=set)
    model_flagged_without: set = field(default_factory=set)
    # Subsets of model_flagged_{with,without} that a judge LLM
    # confirmed actually address a human comment on the same file.
    # Populated only when run(match_mode="judge"); otherwise empty
    # sets and the scorer treats strict-mode recall as unavailable.
    semantic_hits_with: set = field(default_factory=set)
    semantic_hits_without: set = field(default_factory=set)
    file_count: int = 0
    errors: int = 0

    def hits_with(self):
        return self.human_files & self.model_flagged_with

    def hits_without(self):
        return self.human_files & self.model_flagged_without

    def recall(self, with_adapter=True, strict=False):
        if not self.human_files:
            return None
        if strict:
            hits = self.semantic_hits_with if with_adapter else self.semantic_hits_without
        else:
            flagged = self.model_flagged_with if with_adapter else self.model_flagged_without
            hits = self.human_files & flagged
        return len(hits) / len(self.human_files)

    def precision(self, with_adapter=True):
        flagged = self.model_flagged_with if with_adapter else self.model_flagged_without
        if not flagged:
            return None
        return len(self.human_files & flagged) / len(flagged)


# ---------------------------------------------------------------------------
# LLM-as-judge (semantic match)
# ---------------------------------------------------------------------------

_JUDGE_PROMPT = """You are a reviewer evaluating whether a model's code-review output addresses the same concern as a human reviewer's comment on the same file.

Human reviewer's comment on `{file_path}`:
---
{human_comment}
---

Model's output on the same file:
---
{model_output}
---

Does the model's output address the same concern the human raised? Be lenient: if the model raises a related issue or touches the same root cause, count it as a match. Only answer "no" if the model is clearly talking about something different, flagged the wrong thing, or said PASS.

Respond with ONLY valid JSON, no fences:
{{"match": true|false, "why": "one sentence"}}
"""


def _judge_match(file_path, human_comment, model_output, runtime):
    """Ask Haiku whether `model_output` addresses `human_comment`.

    Returns {"match": bool, "why": str} — falls back to
    {"match": False, "why": "judge unavailable"} on any runtime
    failure so the harness never crashes on a transient Haiku blip.
    """
    from planet_maiko.agents.routing import resolve_model

    prompt = _JUDGE_PROMPT.format(
        file_path=file_path,
        human_comment=human_comment[:1200],
        model_output=model_output[:1200],
    )
    try:
        r = runtime.send_json(prompt, timeout=20, model=resolve_model("triage"))
    except Exception as e:
        return {"match": False, "why": f"judge error: {e}"}
    if not r.get("success"):
        return {"match": False, "why": f"judge error: {r.get('error', '?')}"}
    parsed = r.get("parsed")
    if not isinstance(parsed, dict) or "match" not in parsed:
        return {"match": False, "why": "judge returned malformed JSON"}
    return {"match": bool(parsed.get("match")), "why": (parsed.get("why") or "").strip()}


def judge_pr(pr, ground_truth, results, runtime, progress=None):
    """Run the judge on every (file, human_comment, model_output) where
    both sides have content. Returns a dict:

        {
          file_path: {
            "matches": [{"human": "...", "model": "...", "why": "..."}],
            "any_match": True|False,
          }
        }

    Only files that humans commented on AND the model flagged are
    examined — the rest are either "miss" (human only) or "extra"
    (model only), both already captured at file-level.
    """
    out = {}
    for file_path, human_comments in ground_truth.items():
        if file_path == "__review_body__":
            continue
        r = results.get(file_path)
        if not r or r.get("verdict") != "FLAG":
            continue
        model_raw = r.get("raw", "")
        matches = []
        any_match = False
        for body in human_comments:
            verdict = _judge_match(file_path, body, model_raw, runtime)
            matches.append({
                "human": body,
                "model": model_raw,
                "match": verdict["match"],
                "why": verdict["why"],
            })
            if verdict["match"]:
                any_match = True
            if progress:
                progress({"event": "judge_call", "pr": pr.url, "file": file_path, "match": verdict["match"]})
        out[file_path] = {"matches": matches, "any_match": any_match}
    return out


def score_pr(pr, ground_truth, with_results, without_results=None,
             judge_with=None, judge_without=None):
    """Bundle per-file verdicts + human comments into a PRScore.

    judge_with / judge_without are the outputs of `judge_pr()` for the
    two runs (only populated when `match_mode="judge"`). When present,
    semantic_hits_* is filled with the files the judge confirmed as
    addressing the human comment; otherwise those sets stay empty and
    strict recall is reported as "—" in the report.
    """
    s = PRScore(pr=pr)
    # Drop the sentinel review-body key — it's not a real file, it's a
    # signal that the reviewer had PR-level concerns. We don't score on
    # it because neither the model nor the file-level ground truth can
    # match a "whole-PR" note to a specific diff.
    s.human_files = {f for f in ground_truth.keys() if f != "__review_body__"}

    for file_path, result in with_results.items():
        if result["verdict"] == "ERROR":
            s.errors += 1
            continue
        s.file_count += 1
        if result["verdict"] == "FLAG":
            s.model_flagged_with.add(file_path)

    if without_results is not None:
        for file_path, result in without_results.items():
            if result["verdict"] == "FLAG":
                s.model_flagged_without.add(file_path)

    if judge_with:
        s.semantic_hits_with = {fp for fp, info in judge_with.items() if info.get("any_match")}
    if judge_without:
        s.semantic_hits_without = {fp for fp, info in judge_without.items() if info.get("any_match")}

    return s


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(fixture_path, adapter_path, compare_baseline=False, progress=None,
        match_mode="file", refresh_ground_truth=False):
    """Run the full holdout harness.

    Args:
        fixture_path: path to fixture JSON (see `load_fixture`).
        adapter_path: trained LoRA dir.
        compare_baseline: also run each PR without the adapter.
        progress: optional callable(event_dict) for per-PR CLI updates.
        match_mode: "file" (default) or "judge". Judge mode asks an
            LLM per (file, human_comment, model_output) triple whether
            the model's output addresses the human's concern; strict
            recall only counts semantic matches. File mode just counts
            flagged-same-file as a hit.
        refresh_ground_truth: re-fetch PR comments from GitHub even if
            a cache exists. Useful after a PR has been edited.
    """
    fixture = load_fixture(fixture_path)
    prs = fixture["prs"]
    if not prs:
        return {"error": "fixture has no PRs", "fixture_name": fixture["name"]}

    cache = None if refresh_ground_truth else _load_ground_truth_cache(fixture_path)
    ground_truth_by_url = (cache or {}).get("by_url") or {}

    runtime = None
    if match_mode == "judge":
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime()
        if not runtime or not runtime.is_available():
            logger.warning("[eval] Judge mode requested but no runtime available — falling back to file mode")
            match_mode = "file"

    scores = []
    judge_detail_by_pr = {}
    updated_ground_truth = dict(ground_truth_by_url)

    for pr in prs:
        if progress:
            progress({"event": "pr_start", "pr": pr.url})
        files = fetch_pr_files(pr)
        if not files:
            logger.warning(f"[eval] {pr.url}: no reviewable files")
            continue
        if pr.url in ground_truth_by_url and not refresh_ground_truth:
            gt = ground_truth_by_url[pr.url]
        else:
            gt = fetch_ground_truth(pr)
            updated_ground_truth[pr.url] = gt

        with_results = review_pr_files(files, adapter_path)
        without_results = review_pr_files(files, None) if compare_baseline else None

        judge_with = judge_without = None
        if match_mode == "judge":
            judge_with = judge_pr(pr, gt, with_results, runtime, progress=progress)
            if without_results is not None:
                judge_without = judge_pr(pr, gt, without_results, runtime, progress=progress)

        s = score_pr(pr, gt, with_results, without_results,
                     judge_with=judge_with, judge_without=judge_without)
        scores.append(s)
        if judge_with:
            judge_detail_by_pr[pr.url] = {"with": judge_with, "without": judge_without}

        if progress:
            progress({
                "event": "pr_done",
                "pr": pr.url,
                "human_files": len(s.human_files),
                "flagged_with": len(s.model_flagged_with),
                "flagged_without": len(s.model_flagged_without) if compare_baseline else None,
                "semantic_hits_with": len(s.semantic_hits_with) if match_mode == "judge" else None,
                "errors": s.errors,
            })

    # If we fetched fresh data for any PR (no cache or refresh flag),
    # stamp the new snapshot so next run is reproducible.
    if updated_ground_truth != ground_truth_by_url or cache is None:
        _save_ground_truth_cache(fixture_path, updated_ground_truth)

    return {
        "fixture_name": fixture["name"],
        "fixture_description": fixture.get("description", ""),
        "adapter_path": adapter_path,
        "compare_baseline": compare_baseline,
        "match_mode": match_mode,
        "ground_truth_cached_at": (cache or {}).get("cached_at") if cache else datetime.now(timezone.utc).isoformat(),
        "scores": scores,
        "judge_detail": judge_detail_by_pr,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _pct(x):
    return "—" if x is None else f"{x:.0%}"


def format_report(result):
    """Turn a run() result into a markdown report."""
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
        if hits:
            lines.append(f"- Hits: `{', '.join(hits)}`")
        if misses:
            lines.append(f"- Missed: `{', '.join(misses)}`")
        if extras:
            lines.append(f"- Extras: `{', '.join(extras)}`")

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

    return "\n".join(lines)
