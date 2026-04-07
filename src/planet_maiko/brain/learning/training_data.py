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


def extract_training_data(repos, limit_per_repo=200, output_dir=None):
    """Extract training data from PR review comments across repos.

    Args:
        repos: list of "org/repo" strings
        limit_per_repo: max PRs to scan per repo
        output_dir: where to save JSONL (defaults to data/training-data/)

    Returns:
        dict with stats: {pairs, violations, passes, file_path}
    """
    from planet_maiko.paths import data_dir

    if output_dir is None:
        output_dir = os.path.join(data_dir(), "training-data")
    os.makedirs(output_dir, exist_ok=True)

    all_pairs = []

    for repo in repos:
        logger.info(f"[training-data] Scanning {repo}...")
        repo_pairs = _extract_from_repo(repo, limit_per_repo)
        all_pairs.extend(repo_pairs)
        logger.info(f"[training-data] {repo}: {len(repo_pairs)} training pairs")

    if not all_pairs:
        logger.warning("[training-data] No training pairs found")
        return {"pairs": 0, "violations": 0, "passes": 0, "file_path": None}

    # Write JSONL
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    file_path = os.path.join(output_dir, f"dataset-{timestamp}.jsonl")
    with open(file_path, "w", encoding="utf-8") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    violations = sum(1 for p in all_pairs if not p["output"].startswith("PASS"))
    passes = sum(1 for p in all_pairs if p["output"].startswith("PASS"))

    logger.info(f"[training-data] Wrote {len(all_pairs)} pairs to {file_path}")
    logger.info(f"[training-data] {violations} violations, {passes} passes")

    return {
        "pairs": len(all_pairs),
        "violations": violations,
        "passes": passes,
        "file_path": file_path,
        "repos_scanned": len(repos),
    }


def _extract_from_repo(repo, limit):
    """Extract training pairs from a single repo's merged PRs."""
    pairs = []

    # Get merged PRs
    prs = _get_merged_prs(repo, limit)
    logger.info(f"[training-data] {repo}: found {len(prs)} merged PRs")

    for pr in prs:
        number = pr.get("number")
        title = pr.get("title", "")

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

                # Skip approval/praise comments
                lower = body.lower()
                if any(lower.startswith(p) for p in ["lgtm", "looks good", "nice", "great", "+1", "approved"]):
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
                logger.debug(f"[training-data] PR #{number}: violation from review on {path}")
        else:
            # PRs with no review comments → pass examples (clean merge)
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
