#!/usr/bin/env python3
"""Claude Code hook: SessionStart.

Fires once when an agent session boots. Reports the underlying
CLAUDE_SESSION_ID to Maiko so the UI's "View Session" link can find
the transcript file on disk. This replaces what the maiko-channel
MCP server used to do at startup — the only piece of MCP functionality
that wasn't already covered by the Stop hook + CLI commands.

Reads identity from .maiko-env.json in the current working directory.
Uses only stdlib. Non-blocking: 5s timeout, silent on failure (the
session still works, just the View Session link won't resolve).
"""

import json
import os
import sys
import urllib.request


def _read_env():
    env_path = os.path.join(os.getcwd(), ".maiko-env.json")
    if not os.path.exists(env_path):
        return None
    try:
        with open(env_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    env = _read_env()
    if not env:
        return  # Not a Maiko-managed worktree
    job_id = env.get("job_id") or env.get("task_id")
    api_url = env.get("api_url") or "http://localhost:8420/api"
    if not job_id:
        return

    session_id = os.environ.get("CLAUDE_SESSION_ID") or ""
    # Some hook payloads also include session_id; fall back to that.
    if not session_id:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            session_id = payload.get("session_id") or ""
        except Exception:
            pass
    if not session_id.strip():
        return  # Nothing to report

    try:
        body = json.dumps({"session_id": session_id.strip()}).encode()
        req = urllib.request.Request(
            f"{api_url}/agents/{job_id}/session",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # Silent: view-session link is a nice-to-have, not load-bearing


if __name__ == "__main__":
    main()
