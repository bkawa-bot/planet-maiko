#!/usr/bin/env python3
"""Claude Code hook: Stop.

Fires when the agent is about to end its response. Polls the Maiko
inbox for the current task and, if there are unread messages, blocks
the stop and returns the messages as the reason — so the agent
automatically picks them up without the user having to remember to
ping it manually.

Reads identity from .maiko-env.json in the current working directory.
Uses only stdlib. Non-blocking: 5s timeout, silent on failure
(falls through to allow stop).

Stop-hook safety:
- The hook payload includes `stop_hook_active`. When true, a previous
  Stop hook fired this same turn already — we MUST allow the stop
  this time, otherwise the agent loops forever consuming inbox
  messages that don't exist.

Output protocol:
- exit 0, no stdout    => allow stop (default)
- exit 0, JSON stdout  => follow the JSON's `decision` field:
    {"decision": "block", "reason": "<text>"} keeps the agent going
    with `reason` injected as a system message.
"""

import json
import os
import sys
import urllib.request


def _read_payload():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def _read_env():
    env_path = os.path.join(os.getcwd(), ".maiko-env.json")
    if not os.path.exists(env_path):
        return None
    try:
        with open(env_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _fetch_inbox(api_url, task_id):
    url = (
        f"{api_url}/agents/{task_id}/inbox"
        "?unread_only=true&mark_read=true"
    )
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8") or "[]")


def _format(messages):
    """Render messages the way the channel MCP does so the agent sees
    a familiar shape and reads them the same way."""
    lines = [
        "[maiko inbox] You have new message(s) — handle them before stopping:",
        "",
    ]
    for m in messages:
        sender = m.get("sender") or "user"
        mt = m.get("message_type") or "message"
        content = (m.get("content") or "").strip()
        lines.append(f"- [{sender} · {mt}] {content}")
    return "\n".join(lines)


def main():
    payload = _read_payload()

    # Loop guard: if the previous Stop hook already blocked this turn
    # and the agent is trying to stop again, let it through. Without
    # this, an empty/stale inbox could trick us into infinite blocking.
    if payload.get("stop_hook_active"):
        return

    env = _read_env()
    if not env:
        return  # No Maiko identity — not a Maiko-managed worktree
    task_id = env.get("task_id")
    api_url = env.get("api_url") or "http://localhost:8420/api"
    if not task_id:
        return

    try:
        messages = _fetch_inbox(api_url, task_id)
    except Exception:
        return  # Server down or unreachable — allow stop, don't strand the agent

    if not messages:
        return  # Inbox empty, allow stop

    print(json.dumps({
        "decision": "block",
        "reason": _format(messages),
    }))


if __name__ == "__main__":
    main()
