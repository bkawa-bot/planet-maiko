#!/usr/bin/env python3
"""Git pre-commit hook: runs staged diff through the LoRA compliance model.

Installed by Planet Maiko into agent worktrees. All changed files are
batched into a single model call to avoid loading the 8B model per file.

Violations are logged to ~/.local/share/planet-maiko/feedback/pending.jsonl
so `maiko retrain` can resolve outcomes and feed them back into training.

Uses only stdlib + maiko CLI. Non-blocking if the model isn't available.
"""

import json
import os
import re
import subprocess
import sys
import time


# Skip non-code files
SKIP_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".lock", ".css", ".svg", ".png", ".jpg", ".gif"}


def _detect_repo():
    """Detect repo name from git remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        url = result.stdout.strip()
        # Extract org/repo from git URL
        for pattern in [r"github\.com[:/](.+?)(?:\.git)?$", r"([^/]+/[^/]+)\.git$"]:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _find_adapter(agent_id=None, repo=None):
    """Find the best LoRA adapter: repo-specific first, then agent, then latest."""
    try:
        data_dir = os.path.join(
            os.environ.get("MAIKO_DATA_DIR", os.path.join(os.path.expanduser("~"), ".local", "share")),
            "planet-maiko",
        )
        models_dir = os.path.join(data_dir, "models")
        if not os.path.isdir(models_dir):
            return None

        adapters = sorted(os.listdir(models_dir), reverse=True)
        if not adapters:
            return None

        # Prefer repo-specific adapter
        if repo:
            safe_name = repo.replace("/", "--").replace("\\", "--")
            for a in adapters:
                if safe_name in a:
                    return os.path.join(models_dir, a)

        # Then agent-specific
        if agent_id:
            for a in adapters:
                if agent_id in a:
                    return os.path.join(models_dir, a)

        # Fall back to most recent
        return os.path.join(models_dir, adapters[0])
    except Exception:
        return None


def _log_feedback(repo, file_path, diff, model_output, adapter_path):
    """Log a violation to pending.jsonl for later resolution."""
    try:
        data_dir = os.path.join(
            os.environ.get("MAIKO_DATA_DIR", os.path.join(os.path.expanduser("~"), ".local", "share")),
            "planet-maiko", "feedback",
        )
        os.makedirs(data_dir, exist_ok=True)

        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "status": "flagged",
            "repo": repo,
            "file_path": file_path,
            "diff": diff[:2000],
            "model_output": model_output,
            "adapter_path": adapter_path or "",
            "review_id": f"review-{int(time.time())}",
        }

        with open(os.path.join(data_dir, "pending.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Don't block commit on logging failure


def main():
    # Check if we're in a maiko agent worktree
    env_path = os.path.join(os.getcwd(), ".maiko-env.json")
    if not os.path.exists(env_path):
        sys.exit(0)

    with open(env_path) as f:
        env = json.load(f)

    agent_id = env.get("agent_id", "")
    repo = _detect_repo()

    # Get staged diff
    result = subprocess.run(
        ["git", "diff", "--cached", "--diff-filter=ACMR"],
        capture_output=True, text=True, timeout=10,
    )
    diff = result.stdout.strip()
    if not diff or len(diff) < 50:
        sys.exit(0)

    # Split into per-file diffs
    file_diffs = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)

    files_to_review = []
    for file_diff in file_diffs:
        file_diff = file_diff.strip()
        if not file_diff.startswith("diff --git"):
            continue

        match = re.search(r" b/(.+)$", file_diff.split("\n", 1)[0])
        file_path = match.group(1) if match else "unknown"

        ext = os.path.splitext(file_path)[1].lower()
        if ext in SKIP_EXTENSIONS:
            continue

        files_to_review.append({"file_path": file_path, "diff": file_diff})

    if not files_to_review:
        sys.exit(0)

    # Batch all files into a single review call via stdin
    batch_input = ""
    for f in files_to_review:
        batch_input += f"--- {f['file_path']} ---\n{f['diff']}\n\n"

    # Find adapter (prefer repo-specific)
    adapter_path = _find_adapter(agent_id=agent_id, repo=repo)

    try:
        cmd = ["maiko", "review"]
        if agent_id:
            cmd.extend(["--agent", agent_id])

        review = subprocess.run(
            cmd, input=batch_input,
            capture_output=True, text=True, timeout=180,
        )
        output = review.stdout.strip()

        if not output or "VIOLATION" not in output:
            sys.exit(0)

        # Log each violation for feedback resolution
        for f in files_to_review:
            _log_feedback(repo, f["file_path"], f["diff"], output, adapter_path)

        # Found violations
        print("\n=== LoRA Compliance Review ===\n")
        for line in output.split("\n"):
            if line.strip():
                print(f"  {line}")
        print()
        print("Fix violations before committing.")
        print("To bypass: git commit --no-verify\n")
        sys.exit(1)

    except Exception:
        sys.exit(0)  # Don't block on errors


if __name__ == "__main__":
    main()
