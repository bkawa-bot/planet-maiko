#!/usr/bin/env python3
"""Git pre-commit hook: runs staged diff through the LoRA compliance model.

Installed by Planet Maiko into agent worktrees. All changed files are
batched into a single model call to avoid loading the 8B model per file.

Uses only stdlib + maiko CLI. Non-blocking if the model isn't available.
"""

import json
import os
import re
import subprocess
import sys


# Skip non-code files
SKIP_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".lock", ".css", ".svg", ".png", ".jpg", ".gif"}


def main():
    # Check if we're in a maiko agent worktree
    env_path = os.path.join(os.getcwd(), ".maiko-env.json")
    if not os.path.exists(env_path):
        sys.exit(0)

    with open(env_path) as f:
        env = json.load(f)

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

    try:
        review = subprocess.run(
            ["maiko", "review", "--agent", env.get("agent_id", "")],
            input=batch_input,
            capture_output=True, text=True, timeout=180,
        )
        output = review.stdout.strip()

        if not output or "VIOLATION" not in output:
            sys.exit(0)

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
