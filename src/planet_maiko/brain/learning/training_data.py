"""Training data extraction from real PR history.

Extracts code + review comment pairs from GitHub PRs as training data
for LoRA fine-tuning. Real violations from real humans — no synthetic
generation needed.

Training pair format (JSONL):
  {"input": "<code hunk>", "output": "VIOLATION: <review comment>", "repo": "...", "category": "..."}
  {"input": "<clean code>", "output": "PASS", "repo": "...", "category": "..."}
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def extract_training_data(repos, limit_per_repo=200, output_dir=None, exclude_pr_urls=None):
    """Extract training data from PR review comments across repos.

    Args:
        repos: list of "org/repo" strings
        limit_per_repo: max PRs to scan per repo
        output_dir: where to save JSONL (defaults to data/training-data/)
        exclude_pr_urls: list of PR URLs (or "org/repo#N" shorthand) to
            skip during extraction. Used by the holdout-eval harness to
            keep the model from ever seeing the PRs we'll test it on.

    Returns:
        dict with stats: {pairs, violations, passes, file_path}
    """
    from planet_maiko.paths import data_dir

    if output_dir is None:
        output_dir = os.path.join(data_dir(), "training-data")
    os.makedirs(output_dir, exist_ok=True)

    excluded = _parse_excluded_prs(exclude_pr_urls or [])

    all_pairs = []
    pairs_by_repo = {}

    for repo in repos:
        logger.info(f"[training-data] Scanning {repo}...")
        repo_pairs = _extract_from_repo(repo, limit_per_repo, excluded_numbers=excluded.get(repo, set()))
        all_pairs.extend(repo_pairs)
        pairs_by_repo[repo] = repo_pairs
        logger.info(f"[training-data] {repo}: {len(repo_pairs)} training pairs")

    # Pull unincorporated feedback signals into training data
    signal_pairs, signal_ids = _extract_from_signals()
    if signal_pairs:
        all_pairs.extend(signal_pairs)
        for pair in signal_pairs:
            repo = pair.get("repo", "signals")
            pairs_by_repo.setdefault(repo, []).append(pair)
        logger.info(f"[training-data] {len(signal_pairs)} pairs from feedback signals")

    if not all_pairs:
        logger.warning("[training-data] No training pairs found")
        return {"pairs": 0, "violations": 0, "passes": 0, "file_path": None}

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # Write per-repo datasets
    repo_files = {}
    for repo, repo_pairs in pairs_by_repo.items():
        if not repo_pairs:
            continue
        safe_name = repo.replace("/", "--")
        repo_path = os.path.join(output_dir, f"{safe_name}-{timestamp}.jsonl")
        with open(repo_path, "w", encoding="utf-8") as f:
            for pair in repo_pairs:
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")
        repo_files[repo] = repo_path
        logger.info(f"[training-data] Wrote {len(repo_pairs)} pairs to {repo_path}")

    # Write combined dataset
    combined_path = os.path.join(output_dir, f"combined-{timestamp}.jsonl")
    with open(combined_path, "w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    violations = sum(1 for p in all_pairs if not p["output"].startswith("PASS"))
    passes = sum(1 for p in all_pairs if p["output"].startswith("PASS"))

    logger.info(f"[training-data] Wrote {len(all_pairs)} combined pairs to {combined_path}")
    logger.info(f"[training-data] {violations} violations, {passes} passes")

    # Mark incorporated signals
    if signal_ids:
        _mark_signals_incorporated(signal_ids)
        logger.info(f"[training-data] Marked {len(signal_ids)} signals as incorporated")

    return {
        "pairs": len(all_pairs),
        "violations": violations,
        "passes": passes,
        "signals_incorporated": len(signal_ids) if signal_ids else 0,
        "file_path": combined_path,
        "repo_files": repo_files,
        "repos_scanned": len(repos),
    }


def _extract_from_repo(repo, limit, excluded_numbers=None):
    """Extract training pairs from a single repo's merged PRs."""
    pairs = []
    excluded_numbers = excluded_numbers or set()

    # Get merged PRs
    prs = _get_merged_prs(repo, limit)
    logger.info(f"[training-data] {repo}: found {len(prs)} merged PRs")

    for pr in prs:
        number = pr.get("number")
        if number in excluded_numbers:
            logger.info(f"[training-data] {repo}#{number}: excluded (holdout)")
            continue
        title = pr.get("title", "")
        has_feedback = False

        # Get inline review comments (with file positions)
        comments = _get_review_comments(repo, number)

        if comments:
            # PRs with review comments → violation examples
            for comment in comments:
                code_hunk = comment.get("diff_hunk", "")
                body = comment.get("body", "").strip()
                path = comment.get("path", "")

                if not code_hunk or not body or len(body) < 15:
                    continue

                if _is_approval_comment(body):
                    continue

                # Build contextual input: file path, PR context, then code
                context_parts = [f"File: {path}"]
                if title:
                    context_parts.append(f"PR: {title}")
                context_parts.append(f"```\n{code_hunk}\n```")

                pairs.append({
                    "input": "\n".join(context_parts),
                    "output": f"VIOLATION: {body}",
                    "repo": repo,
                    "file_path": path,
                    "pr_number": number,
                    "pr_title": title,
                })
                has_feedback = True
                logger.debug(f"[training-data] PR #{number}: violation from inline comment on {path}")

        # Get review bodies (the overall review comment submitted with approve/request changes)
        reviews = _get_review_bodies(repo, number)
        if reviews:
            diff = _get_pr_diff(repo, number)
            if diff and len(diff) > 50:
                for review_body in reviews:
                    if _is_approval_comment(review_body):
                        continue

                    # Pair each review body with per-file diffs so the model
                    # sees the code that prompted the feedback
                    for file_path, hunk in _split_diff_by_file(diff):
                        if len(hunk) < 30:
                            continue
                        context_parts = [f"File: {file_path}"]
                        if title:
                            context_parts.append(f"PR: {title}")
                        context_parts.append(f"```\n{hunk}\n```")
                        context_parts.append(f"Review context: {review_body[:500]}")

                        pairs.append({
                            "input": "\n".join(context_parts),
                            "output": f"VIOLATION: {review_body}",
                            "repo": repo,
                            "file_path": file_path,
                            "pr_number": number,
                            "pr_title": title,
                            "source": "review_body",
                        })
                    has_feedback = True
                    logger.debug(f"[training-data] PR #{number}: violation from review body")

        if not has_feedback:
            # PRs with no feedback at all → pass examples (clean merge)
            # Split into per-file hunks so PASS examples match violation shape
            diff = _get_pr_diff(repo, number)
            if diff and len(diff) > 50:
                for file_path, hunk in _split_diff_by_file(diff):
                    if len(hunk) < 30:
                        continue
                    context_parts = [f"File: {file_path}"]
                    if title:
                        context_parts.append(f"PR: {title}")
                    context_parts.append(f"```\n{hunk}\n```")

                    pairs.append({
                        "input": "\n".join(context_parts),
                        "output": "PASS",
                        "repo": repo,
                        "file_path": file_path,
                        "pr_number": number,
                        "pr_title": title,
                    })
                logger.debug(f"[training-data] PR #{number}: clean merge → pass example")

    return pairs


def _extract_from_signals():
    """Pull unincorporated feedback signals that have code context.

    One signal can have multiple examples (same comment left on multiple
    files/hunks). Each example becomes its own training pair — that's
    the point of keeping them separate: the model gets diverse code
    snippets paired with the same human reaction.
    """
    try:
        from planet_maiko.models.signal import Signal

        # A signal is training-usable if it has ANY source of code
        # context — either a filled code_context (old rows / non-inline
        # sources) or at least one entry in examples (new rows).
        signals = Signal.query.filter(Signal.incorporated_at.is_(None)).all()

        pairs = []
        signal_ids = []
        for s in signals:
            # Build the example list. Prefer examples[] when present;
            # fall back to the single code_context column for back-compat.
            examples = []
            for ex in (s.examples or []):
                hunk = (ex.get("diff_hunk") or "").strip()
                if not hunk:
                    continue
                examples.append({
                    "path": ex.get("path") or s.file_path or "",
                    "diff_hunk": hunk,
                })
            if not examples and s.code_context:
                examples.append({
                    "path": s.file_path or "",
                    "diff_hunk": s.code_context,
                })
            if not examples:
                continue

            output = "PASS" if s.severity == "rejected" else f"VIOLATION: [{s.category}] {s.text}"

            for ex in examples:
                context_parts = []
                if ex["path"]:
                    context_parts.append(f"File: {ex['path']}")
                if s.repo:
                    context_parts.append(f"Repo: {s.repo}")
                context_parts.append(f"```\n{ex['diff_hunk']}\n```")

                pairs.append({
                    "input": "\n".join(context_parts),
                    "output": output,
                    "repo": s.repo or "unknown",
                    "file_path": ex["path"],
                    "source": "signal",
                    "signal_id": s.id,
                })
            signal_ids.append(s.id)

        return pairs, signal_ids
    except Exception as e:
        logger.debug(f"[training-data] Could not extract from signals: {e}")
        return [], []


def _mark_signals_incorporated(signal_ids):
    """Mark signals as incorporated into a training dataset."""
    try:
        from planet_maiko.models.signal import Signal
        from planet_maiko.database import db

        Signal.query.filter(Signal.id.in_(signal_ids)).update(
            {"incorporated_at": datetime.now(timezone.utc)},
            synchronize_session=False,
        )
        db.session.commit()
    except Exception as e:
        logger.warning(f"[training-data] Could not mark signals incorporated: {e}")


def _parse_excluded_prs(urls):
    """Turn a list of PR URLs into {repo: set(numbers)} for fast lookup.

    Accepts both the full URL form (https://github.com/org/repo/pull/123)
    and the short form (org/repo#123). Unparseable entries are dropped
    with a warning — the caller's intent is to *exclude* so we fail loud
    but don't block extraction.
    """
    import re as _re
    out = {}
    for raw in urls:
        s = (raw or "").strip()
        if not s:
            continue
        m = _re.match(r"https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)", s)
        if not m:
            m = _re.match(r"([^/]+/[^/]+)#(\d+)", s)
        if not m:
            logger.warning(f"[training-data] Could not parse exclude URL: {raw!r}")
            continue
        repo, number = m.group(1), int(m.group(2))
        out.setdefault(repo, set()).add(number)
    return out


def _split_diff_by_file(diff_text):
    """Split a unified diff into (file_path, hunk) pairs."""
    import re
    chunks = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk.startswith("diff --git"):
            continue
        # Extract file path from "diff --git a/path b/path"
        first_line = chunk.split("\n", 1)[0]
        match = re.search(r" b/(.+)$", first_line)
        file_path = match.group(1) if match else "unknown"
        yield file_path, chunk


def _get_merged_prs(repo, limit):
    """Fetch merged PRs from a repo."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--state", "merged",
             "--limit", str(limit),
             "--json", "number,title,mergedAt"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.warning(f"[training-data] Failed to list PRs for {repo}: {e}")
    return []


def _is_approval_comment(body):
    """Return True if the comment is approval/praise noise, not actionable feedback."""
    lower = body.strip().lower()
    if any(lower.startswith(p) for p in ["lgtm", "looks good", "nice", "great", "+1", "approved"]):
        return True
    approval_phrases = ["lgtm", "looks good to me", "ship it", "no concerns", "no issues"]
    if any(p in lower for p in approval_phrases) and len(body) < 100:
        return True
    # Bot comments and boilerplate
    if any(p in lower for p in [
        "<!-- sidekick", "<!-- model", "<sub>", "review complete. no comments",
        "no actionable rule", "have feedback for sidekick", "react to this review",
    ]):
        return True
    return False


def _get_review_bodies(repo, pr_number):
    """Fetch review bodies (the overall comment submitted with a review).

    Filters to reviews that have substantive body text and are not
    pure approvals. Returns list of body strings.
    """
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{pr_number}/reviews",
             "--jq", '[.[] | select(.body != null and .body != "") | .body]'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            bodies = json.loads(result.stdout)
            # Filter out short/empty bodies, pure approvals, and bot comments
            return [b for b in bodies
                    if len(b.strip()) >= 30
                    and not _is_approval_comment(b)
                    and not b.strip().startswith("<!--")
                    and not b.strip().startswith("<sub>")]
    except Exception as e:
        logger.debug(f"[training-data] No review bodies for {repo}#{pr_number}: {e}")
    return []


def _get_review_comments(repo, pr_number):
    """Fetch inline review comments for a PR (with diff hunks)."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{pr_number}/comments"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            raw = json.loads(result.stdout)
            return [
                {
                    "body": c.get("body", ""),
                    "diff_hunk": c.get("diff_hunk", ""),
                    "path": c.get("path", ""),
                    "position": c.get("position"),
                }
                for c in raw
            ]
    except Exception as e:
        logger.debug(f"[training-data] No review comments for {repo}#{pr_number}: {e}")
    return []


def _get_pr_diff(repo, pr_number):
    """Fetch the diff for a PR."""
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", repo],
            capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return ""


def list_datasets(output_dir=None):
    """List all generated training datasets."""
    from planet_maiko.paths import data_dir

    if output_dir is None:
        output_dir = os.path.join(data_dir(), "training-data")

    if not os.path.isdir(output_dir):
        return []

    datasets = []
    for f in sorted(os.listdir(output_dir), reverse=True):
        if f.endswith(".jsonl"):
            path = os.path.join(output_dir, f)
            size = os.path.getsize(path)
            # Count lines
            with open(path) as fp:
                lines = sum(1 for _ in fp)
            datasets.append({
                "filename": f,
                "path": path,
                "size_bytes": size,
                "examples": lines,
                "created_at": os.path.getmtime(path),
            })

    return datasets


def get_dataset_stats(file_path):
    """Get detailed stats for a dataset file."""
    if not os.path.exists(file_path):
        return None

    stats = {"total": 0, "violations": 0, "passes": 0, "by_repo": {}, "by_category": {}}

    with open(file_path) as f:
        for line in f:
            try:
                pair = json.loads(line)
                stats["total"] += 1
                if pair["output"].startswith("PASS"):
                    stats["passes"] += 1
                else:
                    stats["violations"] += 1

                repo = pair.get("repo", "unknown")
                stats["by_repo"][repo] = stats["by_repo"].get(repo, 0) + 1
            except json.JSONDecodeError:
                pass

    return stats
