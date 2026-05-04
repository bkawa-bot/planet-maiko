"""Cross-platform helpers for launching agents in a real terminal
window and locating the Claude Code session log on disk. Used by the
"open terminal" and "resume session" endpoints in agents_api.

Originally inline in agents_api.py — extracted so the platform-
specific shell-quoting paths live in one focused file instead of in
the middle of a 1900-line route handler.
"""

import os
import subprocess
import sys
import tempfile


def _launch_terminal(cmd):
    """Open a new terminal window that runs ``cmd`` and stays open.

    All three platforms route through a temp script file (.sh on
    macOS / Linux, .bat on Windows). When ``cmd`` contains double
    quotes -- and ours always does, because we pass an initial
    prompt to claude in quotes -- the platform-native incantations
    all break in different ways:

      * macOS: osascript's ``do script "..."`` collapses on the
        first inner quote, emitting AppleScript's "an identifier
        can't go after this" error before anything runs.
      * Windows: ``cmd /c start cmd /k "..."`` mispairs the inner
        quotes with start's title arg.
      * Linux: bash -c handles quotes ok but is inconsistent across
        terminals.

    A script file's contents are plain text -- no shell or
    AppleScript parser sees the quotes. We just hand the launcher
    a path.
    """
    if sys.platform == "darwin":
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sh", delete=False, encoding="utf-8",
        ) as f:
            f.write("#!/bin/bash\n")
            f.write(cmd + "\n")
            sh_path = f.name
        os.chmod(sh_path, 0o755)
        # Telling Terminal to "do script <path>" runs the file; no
        # quotes inside the script body to worry about. If the user
        # closes the window the .sh stays in /tmp and is reaped by
        # the OS's normal temp-file cleanup.
        subprocess.Popen([
            "osascript", "-e",
            f'tell application "Terminal" to do script "{sh_path}"',
        ])
        return

    if sys.platform == "win32":
        with tempfile.NamedTemporaryFile(
            "w", suffix=".bat", delete=False, encoding="utf-8",
        ) as f:
            f.write("@echo off\r\n")
            f.write(cmd + "\r\n")
            bat_path = f.name
        # `start "" cmd /k <bat>` — the empty "" is required as the
        # window-title placeholder, otherwise start treats the next
        # quoted thing as the title and the actual command never runs.
        subprocess.Popen(
            ["cmd", "/c", "start", "", "cmd", "/k", bat_path],
            shell=False,
        )
        return

    # Linux: same approach for consistency.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8",
    ) as f:
        f.write("#!/bin/bash\n")
        f.write(cmd + "\n")
        sh_path = f.name
    os.chmod(sh_path, 0o755)
    for term in ["gnome-terminal", "xterm", "konsole"]:
        try:
            subprocess.Popen([term, "--", "bash", sh_path])
            return
        except FileNotFoundError:
            continue


def _find_claude_session_file(working_path, session_id):
    """Find the Claude Code session JSONL file for a given worktree + session ID.

    Claude stores sessions at ~/.claude/projects/{escaped-path}/{session_id}.jsonl
    where escaped-path replaces /, \\, and : each independently with -.
    On Windows, "C:\\Users\\foo" becomes "C--Users-foo" (double dash from : + \\).
    """
    if not working_path or not session_id:
        return None
    abs_path = os.path.abspath(working_path)
    escaped = abs_path.replace(":", "-").replace("\\", "-").replace("/", "-")
    candidates = [
        os.path.expanduser(f"~/.claude/projects/{escaped}/{session_id}.jsonl"),
        os.path.expanduser(f"~/.config/claude/projects/{escaped}/{session_id}.jsonl"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None
