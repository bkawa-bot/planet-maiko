#!/usr/bin/env python3
"""Planet Maiko bootstrap. Checks prereqs, creates venv, installs deps.

Usage:
    python3 bootstrap.py

After it finishes, activate the venv and run `maiko up`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
VENV_DIR = REPO_ROOT / ".venv"
FRONTEND_DIR = REPO_ROOT / "frontend"


def step(msg: str) -> None:
    print(f"\n> {msg}")


def ok(msg: str) -> None:
    print(f"  {msg}")


def warn(msg: str) -> None:
    print(f"  ! {msg}", file=sys.stderr)


def die(msg: str) -> None:
    print(f"  x {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list, cwd: Path | None = None) -> None:
    """Run a command, dying on non-zero exit."""
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        die(f"Command failed (exit {result.returncode}): {' '.join(map(str, cmd))}")


# ----- prereq checks -----


def check_python() -> None:
    step("Checking Python version")
    if sys.version_info < (3, 10):
        die(
            f"Python 3.10+ required (got {sys.version_info.major}.{sys.version_info.minor}). "
            "Install from https://python.org or via your package manager."
        )
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor} found")


def check_node() -> None:
    step("Checking Node.js version")
    if shutil.which("node") is None:
        die(
            "Node.js is not installed. Install Node 18+ from https://nodejs.org "
            "(or `brew install node`, `nvm install 18`)."
        )
    raw = subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip()
    match = re.match(r"v?(\d+)\.", raw)
    if not match:
        die(f"Couldn't parse Node version: {raw!r}")
    if int(match.group(1)) < 18:
        die(f"Node 18+ required (got {raw}). Upgrade via your package manager or `nvm install 18`.")
    ok(f"Node {raw} found")


def check_gh() -> None:
    step("Checking GitHub CLI (gh)")
    if shutil.which("gh") is None:
        warn(
            "`gh` not on PATH. Maiko uses it for repo discovery + worktrees. "
            "Install from https://cli.github.com before running `maiko up`."
        )
        return
    ok("gh present")


def check_claude() -> None:
    step("Checking Claude Code")
    if shutil.which("claude") is None:
        warn(
            "`claude` not on PATH. Maiko's agents run on Claude Code. "
            "Install from https://docs.claude.com/en/docs/claude-code/quickstart "
            "before running `maiko up`."
        )
        return
    ok("claude present")


def check_gh_auth() -> None:
    step("Checking GitHub auth")
    if shutil.which("gh") is None:
        return  # already warned in check_gh
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode != 0:
        warn("Not authenticated. Run `gh auth login` before launching Maiko.")
        return
    ok("gh authenticated")


# ----- install steps -----


def create_venv() -> None:
    step(f"Creating venv at {VENV_DIR}")
    if VENV_DIR.exists():
        ok("venv already exists, reusing")
        return
    run([sys.executable, "-m", "venv", str(VENV_DIR)])
    ok("venv created")


def venv_bin(name: str) -> Path:
    """Path to an executable inside the venv (handles .exe on Windows)."""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / f"{name}.exe"
    return VENV_DIR / "bin" / name


def venv_python() -> Path:
    return venv_bin("python")


def install_python_deps() -> None:
    step("Installing Python deps (pip install -e .)")
    run([str(venv_python()), "-m", "pip", "install", "-e", "."], cwd=REPO_ROOT)
    ok("Python deps installed")


def install_npm_deps() -> None:
    step("Installing frontend deps (npm install)")
    npm = shutil.which("npm")
    if npm is None:
        die("`npm` not on PATH but Node is. Reinstall Node from nodejs.org.")
    run([npm, "install"], cwd=FRONTEND_DIR)
    ok("Frontend deps installed")


# ----- launch -----


def launch_maiko() -> None:
    """Hand off to `maiko up` so the first thing the user sees is Maiko."""
    step("Launching Maiko (ctrl+C to stop)")
    maiko = venv_bin("maiko")
    if not maiko.exists():
        activate = r".venv\Scripts\activate" if os.name == "nt" else "source .venv/bin/activate"
        warn(
            f"Couldn't find {maiko}. Activate the venv and run `maiko up` manually:\n"
            f"    {activate}\n    maiko up"
        )
        return
    print()
    try:
        subprocess.run([str(maiko), "up"], cwd=REPO_ROOT)
    except KeyboardInterrupt:
        pass


# ----- entry point -----


def main() -> None:
    print("Planet Maiko bootstrap")
    print("======================")
    check_python()
    check_node()
    check_gh()
    check_claude()
    create_venv()
    install_python_deps()
    install_npm_deps()
    check_gh_auth()
    launch_maiko()


if __name__ == "__main__":
    main()
