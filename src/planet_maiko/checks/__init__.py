"""Checker bridge — run a repo's own checkers inside a worktree.

The verification community's current frame ("vibe coding vs vericoding",
Tao / de Moura / Cook) maps naturally onto Maiko's flow: when an agent
says `ready_for_review`, we want them to have actually run the tests
and linters first, not just "claimed they're done." This module is the
lightweight version of that — not Lean / Dafny / TLA+, just whatever
checker the repo already uses.

Two modes:

1. Auto-detect (default): look at what files exist in the worktree and
   pick common checkers (pytest for Python, npm test for Node, cargo
   check for Rust, go test for Go, tsc for TypeScript, ruff for Python
   linting). Zero config.

2. Override: if `.maiko/checks.json` exists at the repo root, use the
   declared commands verbatim. Shape:

       {
         "checks": [
           {"name": "unit tests", "command": "pytest -x"},
           {"name": "lint", "command": "ruff check ."}
         ]
       }

Each check runs with a timeout, captures stdout/stderr, and reports
status (pass/fail/error/timeout) plus a short tail of output. Results
are structured so Maiko can surface them in the UI and agents can
parse them over MCP.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

_OUTPUT_TAIL_CHARS = 1200
_DEFAULT_TIMEOUT = 120


def detect_checks(repo_path: str) -> list[dict]:
    """Build the list of checks for a repo. Honors `.maiko/checks.json`
    if present, otherwise auto-detects from file presence.

    Returns: list of {"name": str, "command": str}.
    """
    override = _load_override(repo_path)
    if override:
        return override
    return _auto_detect(repo_path)


def _load_override(repo_path: str) -> list[dict]:
    path = os.path.join(repo_path, ".maiko", "checks.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"[checks] Could not read {path}: {e}")
        return []
    checks = data.get("checks") or []
    out = []
    for c in checks:
        name = (c.get("name") or "").strip()
        command = (c.get("command") or "").strip()
        if name and command:
            out.append({"name": name, "command": command})
    return out


def _auto_detect(repo_path: str) -> list[dict]:
    """Pick sensible defaults based on what's in the repo. Conservative:
    we'd rather run nothing than run something that blows up — each
    detector must see a strong positive signal (not just the presence
    of a language file)."""
    checks = []
    has = lambda *parts: os.path.exists(os.path.join(repo_path, *parts))

    # Python — prefer ruff if configured, pytest if there's a tests dir
    if has("pyproject.toml") or has("setup.py") or has("requirements.txt"):
        if has("pyproject.toml") or has("ruff.toml") or has(".ruff.toml"):
            if shutil.which("ruff"):
                checks.append({"name": "ruff", "command": "ruff check ."})
        if has("tests") or has("test") or _has_any(repo_path, ("test_", "_test.py")):
            if shutil.which("pytest"):
                checks.append({"name": "pytest", "command": "pytest -x --tb=short"})

    # Node / TypeScript
    if has("package.json"):
        pkg = _read_json(os.path.join(repo_path, "package.json")) or {}
        scripts = pkg.get("scripts") or {}
        pkg_manager = _pkg_manager(repo_path)
        if scripts.get("lint") and pkg_manager:
            checks.append({"name": "lint", "command": f"{pkg_manager} run lint"})
        if scripts.get("test") and pkg_manager:
            checks.append({"name": "test", "command": f"{pkg_manager} test --silent"})
        if has("tsconfig.json") and shutil.which("tsc"):
            checks.append({"name": "typecheck", "command": "tsc --noEmit"})

    # Rust
    if has("Cargo.toml") and shutil.which("cargo"):
        checks.append({"name": "cargo check", "command": "cargo check --quiet"})
        if shutil.which("cargo") and _has_clippy():
            checks.append({"name": "clippy", "command": "cargo clippy --quiet -- -D warnings"})
        checks.append({"name": "cargo test", "command": "cargo test --quiet"})

    # Go
    if has("go.mod") and shutil.which("go"):
        checks.append({"name": "go vet", "command": "go vet ./..."})
        checks.append({"name": "go test", "command": "go test ./..."})

    return checks


def _has_any(repo_path: str, prefixes_or_suffixes: tuple) -> bool:
    """Shallow check — does any top-level file in repo_path or its
    immediate tests/ dir look test-shaped?"""
    for root in (repo_path, os.path.join(repo_path, "tests"), os.path.join(repo_path, "test")):
        if not os.path.isdir(root):
            continue
        try:
            for name in os.listdir(root):
                for marker in prefixes_or_suffixes:
                    if name.startswith(marker) or name.endswith(marker):
                        return True
        except OSError:
            continue
    return False


def _pkg_manager(repo_path: str) -> str | None:
    for lock, cmd in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm")):
        if os.path.exists(os.path.join(repo_path, lock)) and shutil.which(cmd):
            return cmd
    if shutil.which("npm"):
        return "npm"
    return None


def _has_clippy() -> bool:
    try:
        r = subprocess.run(["cargo", "clippy", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def run_checks(repo_path: str, checks: list[dict] | None = None, timeout: int = _DEFAULT_TIMEOUT) -> dict:
    """Execute every check and summarize.

    Returns:

        {
          "repo_path": str,
          "checks": [
            {"name": ..., "command": ..., "status": "pass"|"fail"|"timeout"|"error"|"missing",
             "exit_code": int | None, "output_tail": str},
            ...
          ],
          "summary": {"total": int, "passed": int, "failed": int, "blocked": bool},
        }

    `blocked` is True if any check didn't pass — agents use this to
    decide whether it's honest to claim `ready_for_review`.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return {"repo_path": repo_path, "checks": [], "summary": {"total": 0, "passed": 0, "failed": 0, "blocked": False},
                "error": "repo_path is not a directory"}

    if checks is None:
        checks = detect_checks(repo_path)

    results = []
    for spec in checks:
        results.append(_run_one(spec, repo_path, timeout))

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] != "pass")
    return {
        "repo_path": repo_path,
        "checks": results,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "blocked": failed > 0,
        },
    }


def _run_one(spec: dict, cwd: str, timeout: int) -> dict:
    """Run a single check. Uses shell=True on purpose — the config /
    auto-detect produce a full command line (e.g. `npm run lint`) that
    would be awkward to split by hand. This is fine because the input
    comes from repo-local config or Maiko's auto-detect, not from user
    HTTP input."""
    name = spec.get("name") or "check"
    command = spec.get("command") or ""
    if not command:
        return {"name": name, "command": command, "status": "error", "exit_code": None, "output_tail": "empty command"}

    try:
        r = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"name": name, "command": command, "status": "timeout", "exit_code": None,
                "output_tail": f"(no output within {timeout}s)"}
    except FileNotFoundError:
        return {"name": name, "command": command, "status": "missing", "exit_code": None,
                "output_tail": "command not found on PATH"}
    except Exception as e:
        return {"name": name, "command": command, "status": "error", "exit_code": None, "output_tail": str(e)[:200]}

    tail = _tail(r.stdout + ("\n" + r.stderr if r.stderr else ""))
    status = "pass" if r.returncode == 0 else "fail"
    return {
        "name": name,
        "command": command,
        "status": status,
        "exit_code": r.returncode,
        "output_tail": tail,
    }


def _tail(s: str) -> str:
    s = (s or "").rstrip()
    if len(s) <= _OUTPUT_TAIL_CHARS:
        return s
    return "…\n" + s[-_OUTPUT_TAIL_CHARS:]


# ---------------------------------------------------------------------------
# Promotion: LoRA-to-spec ladder step
# ---------------------------------------------------------------------------

def append_check(repo_path: str, name: str, command: str) -> dict:
    """Append a check to `<repo_path>/.maiko/checks.json`, creating the
    file and the `.maiko/` dir if they don't exist yet.

    This is the bridge between informal Learnings (patterns a LoRA
    has noticed from PR reviews) and enforced checks every future
    agent must pass via check_code(). Promotion here moves a rule
    up the spec ladder from "advice the model has internalized" to
    "failure mode CI catches."

    Returns the updated checks list. Duplicates (same command) are
    replaced rather than added, so re-promoting the same learning
    updates instead of piling up duplicates.
    """
    name = (name or "").strip()
    command = (command or "").strip()
    if not name or not command:
        raise ValueError("name and command are both required")
    if not os.path.isdir(repo_path):
        raise ValueError(f"repo_path is not a directory: {repo_path}")

    cfg_dir = os.path.join(repo_path, ".maiko")
    cfg_path = os.path.join(cfg_dir, "checks.json")
    os.makedirs(cfg_dir, exist_ok=True)

    data = {"checks": []}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("checks"), list):
                data = loaded
        except Exception as e:
            logger.warning(f"[checks] .maiko/checks.json at {cfg_path} is malformed, replacing: {e}")

    existing = data["checks"]
    # De-dup by exact command match — re-promoting the same rule just
    # updates the name in place rather than adding a duplicate entry.
    replaced = False
    for i, c in enumerate(existing):
        if (c.get("command") or "").strip() == command:
            existing[i] = {"name": name, "command": command}
            replaced = True
            break
    if not replaced:
        existing.append({"name": name, "command": command})

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data
