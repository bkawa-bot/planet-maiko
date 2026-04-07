#!/usr/bin/env python3
"""Git pre-commit hook: runs staged diff through the LoRA compliance model.

Installed by Planet Maiko into agent worktrees. If the model finds
violations, the commit is blocked and the violations are printed.

Uses only stdlib + maiko CLI. Non-blocking if the model isn't available.
"""

import json
import os
import subprocess
import sys


def main():
    # Check if we're in a maiko agent worktree
    env_path = os.path.join(os.getcwd(), ".maiko-env.json")
    if not os.path.exists(env_path):
        sys.exit(0)  # Not a maiko worktree, allow commit

    with open(env_path) as f:
        env = json.load(f)

    # Get staged diff
    result = subprocess.run(
        ["git", "diff", "--cached", "--diff-filter=ACMR"],
        capture_output=True, text=True, timeout=10,
    )
    diff = result.stdout.strip()
    if not diff or len(diff) < 50:
        sys.exit(0)  # No meaningful diff

    # Split into per-file diffs and review each
    import re
    file_diffs = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)

    violations = []
    for file_diff in file_diffs:
        file_diff = file_diff.strip()
        if not file_diff.startswith("diff --git"):
            continue

        # Extract file path
        match = re.search(r" b/(.+)$", file_diff.split("\n", 1)[0])
        file_path = match.group(1) if match else "unknown"

        # Skip non-code files
        if any(file_path.endswith(ext) for ext in [".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".lock", ".css"]):
            continue

        # Run review via maiko CLI
        try:
            review = subprocess.run(
                ["maiko", "review", "--agent", env.get("agent_id", "")],
                input=file_diff,
                capture_output=True, text=True, timeout=120,
            )
            output = review.stdout.strip()
            if output and "VIOLATION" in output:
                violations.append({"file": file_path, "review": output})
        except Exception:
            continue  # Don't block on errors

    if violations:
        print("\n=== LoRA Compliance Review ===\n")
        for v in violations:
            print(f"  {v['file']}:")
            for line in v["review"].split("\n"):
                print(f"    {line}")
            print()
        print(f"{len(violations)} violation(s) found. Fix before committing.")
        print("To bypass: git commit --no-verify\n")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
