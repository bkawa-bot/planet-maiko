#!/usr/bin/env python3
"""Claude Code hook: PostToolUse — LoRA compliance review after git commit.

Fires after Bash commands matching 'git commit'. Reviews the committed
changes through the LoRA model and returns exit code 2 if violations
are found, which tells Claude to fix them.

This replaces the git pre-commit hook approach — Claude gets the feedback
directly in its context and can act on it immediately.
"""

import json
import os
import re
import subprocess
import sys
import time


SKIP_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".lock", ".css", ".svg", ".png", ".jpg", ".gif"}


def _detect_repo():
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        url = result.stdout.strip()
        for pattern in [r"github\.com[:/](.+?)(?:\.git)?$", r"([^/]+/[^/]+)\.git$"]:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def _log_feedback(repo, file_path, diff, model_output, adapter_path):
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
        pass


def main():
    try:
        # Read hook payload from stdin
        payload = json.loads(sys.stdin.read())
        tool_input = payload.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only fire on git commit
        if "git commit" not in command:
            sys.exit(0)

        # Get the diff of the last commit
        result = subprocess.run(
            ["git", "diff", "HEAD~1", "--diff-filter=ACMR"],
            capture_output=True, text=True, timeout=10,
        )
        diff = result.stdout.strip()
        if not diff or len(diff) < 50:
            sys.exit(0)

        # Filter to code files only
        file_diffs = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
        code_diffs = []
        file_paths = []

        for file_diff in file_diffs:
            file_diff = file_diff.strip()
            if not file_diff.startswith("diff --git"):
                continue
            match = re.search(r" b/(.+)$", file_diff.split("\n", 1)[0])
            file_path = match.group(1) if match else "unknown"
            ext = os.path.splitext(file_path)[1].lower()
            if ext in SKIP_EXTENSIONS:
                continue
            code_diffs.append(file_diff)
            file_paths.append(file_path)

        if not code_diffs:
            sys.exit(0)

        # Batch all code into single review
        batch_input = ""
        for fp, d in zip(file_paths, code_diffs):
            batch_input += f"--- {fp} ---\n{d}\n\n"

        # Read agent ID from env file if available
        agent_arg = []
        env_path = os.path.join(os.getcwd(), ".maiko-env.json")
        if os.path.exists(env_path):
            with open(env_path) as f:
                env = json.load(f)
            if env.get("agent_id"):
                agent_arg = ["--agent", env["agent_id"]]

        # Run review
        review = subprocess.run(
            ["maiko", "review"] + agent_arg,
            input=batch_input,
            capture_output=True, text=True, timeout=180,
        )
        output = review.stdout.strip()

        if not output or "VIOLATION" not in output:
            sys.exit(0)

        # Log feedback for future training
        repo = _detect_repo()
        for fp, d in zip(file_paths, code_diffs):
            _log_feedback(repo, fp, d, output, None)

        # Exit 2 = tell Claude to fix the issues
        # stderr becomes feedback to Claude
        violations = "\n".join(f"  {line}" for line in output.split("\n") if line.strip())
        print(
            f"LoRA compliance review found violations in this commit:\n\n"
            f"{violations}\n\n"
            f"Please fix these violations and commit again.",
            file=sys.stderr,
        )
        sys.exit(2)

    except subprocess.TimeoutExpired:
        sys.exit(0)
    except Exception:
        sys.exit(0)  # Don't block on errors


if __name__ == "__main__":
    main()
