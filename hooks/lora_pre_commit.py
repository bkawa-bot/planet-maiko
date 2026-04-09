#!/usr/bin/env python3
"""Git pre-commit hook: runs staged diff through the LoRA compliance model.

Works standalone in any repo — no maiko server or agent worktree required.
Loads the most recent adapter from ~/.local/share/planet-maiko/models/.

Install:
    # Symlink into your repo
    ln -sf ~/src/planet-maiko/hooks/lora_pre_commit.py /path/to/repo/.git/hooks/pre-commit

    # Or set globally
    git config --global core.hooksPath ~/src/planet-maiko/hooks

Non-blocking: if the model isn't available, the commit proceeds.
"""

import json
import os
import re
import subprocess
import sys

SKIP_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".lock",
    ".css", ".svg", ".png", ".jpg", ".gif", ".xml", ".properties",
}

MODELS_DIR = os.path.expanduser("~/.local/share/planet-maiko/models")
BASE_MODEL = "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit"


def find_adapter():
    """Find the most recent trained adapter."""
    if not os.path.isdir(MODELS_DIR):
        return None
    adapters = sorted(os.listdir(MODELS_DIR), reverse=True)
    for a in adapters:
        path = os.path.join(MODELS_DIR, a)
        if os.path.exists(os.path.join(path, "adapters.safetensors")):
            return path
    return None


def get_staged_files():
    """Get staged file diffs, skipping non-code files."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--diff-filter=ACMR"],
        capture_output=True, text=True, timeout=10,
    )
    diff = result.stdout.strip()
    if not diff or len(diff) < 50:
        return []

    file_diffs = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    files = []
    for file_diff in file_diffs:
        file_diff = file_diff.strip()
        if not file_diff.startswith("diff --git"):
            continue
        match = re.search(r" b/(.+)$", file_diff.split("\n", 1)[0])
        file_path = match.group(1) if match else "unknown"
        ext = os.path.splitext(file_path)[1].lower()
        if ext in SKIP_EXTENSIONS:
            continue
        files.append({"file_path": file_path, "diff": file_diff})
    return files


def review(files, adapter_path):
    """Load model and review all files in one pass."""
    from mlx_lm import load, generate

    model, tokenizer = load(BASE_MODEL, adapter_path=adapter_path)

    results = []
    for f in files:
        code_input = f"File: {f['file_path']}\n```\n{f['diff']}\n```"
        messages = [
            {"role": "system", "content": "You are a code review assistant. Given a code change with its file path and PR context, identify violations of coding standards, missing edge cases, security issues, or other problems. Respond PASS if the code is clean."},
            {"role": "user", "content": code_input},
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        response = generate(model, tokenizer, prompt=prompt, max_tokens=256)
        if "VIOLATION" in response:
            results.append({"file": f["file_path"], "review": response.strip()})

    return results


def main():
    adapter_path = find_adapter()
    if not adapter_path:
        sys.exit(0)

    files = get_staged_files()
    if not files:
        sys.exit(0)

    try:
        violations = review(files, adapter_path)
    except Exception:
        sys.exit(0)  # Don't block on errors

    if not violations:
        sys.exit(0)

    print("\n=== LoRA Compliance Review ===\n")
    for v in violations:
        print(f"  {v['file']}:")
        for line in v["review"].split("\n"):
            if line.strip():
                print(f"    {line}")
        print()
    print(f"{len(violations)} violation(s) found. Fix before committing.")
    print("To bypass: git commit --no-verify\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
