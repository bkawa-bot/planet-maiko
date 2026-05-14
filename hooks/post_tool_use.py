#!/usr/bin/env python3
"""Claude Code hook: PostToolUse (Bash matcher).

Fires after every Bash tool use. Does two things:

1. Reports git commit / push events to Maiko so the dashboard stays
   in sync.
2. Polls the Maiko inbox and, if there are unread messages, surfaces
   them to the agent via a `decision: block` response so the agent
   picks them up before its next action. This is the post-MCP
   equivalent of the channel server's mid-flight push notifications —
   without it, agents only see new messages when they settle (via the
   Stop hook), which means up to a full turn of latency on user pings.
   The agent action that JUST ran isn't undone; the block applies to
   the next thing the agent would do.

Reads identity from .maiko-env.json in the current working directory.
Uses only stdlib. Non-blocking: 5s timeout, silent on failure.
"""

import json
import os
import re
import sys
import urllib.request


def _report_git_event(job_id, agent_id, api_url, command):
    is_commit = "git commit" in command
    is_push = "git push" in command
    if not is_commit and not is_push:
        return

    message = ""
    if is_commit:
        m = re.search(r'-m\s+["\']([^"\']+)["\']', command)
        if not m:
            m = re.search(r'-m\s+(\S+)', command)
        if m:
            message = m.group(1)

    event = "git_commit" if is_commit else "git_push"
    body = json.dumps({
        "job_id": job_id,
        "agent_id": agent_id,
        "event": event,
        "message": message or f"Agent ran: {event}",
    }).encode()
    req = urllib.request.Request(
        f"{api_url}/hooks/post-tool-use",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)


def _poll_inbox(job_id, api_url):
    """Return unread messages (marked-read on the server side), or [] on any failure."""
    try:
        url = f"{api_url}/agents/{job_id}/inbox?unread_only=true&mark_read=true"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8") or "[]")
    except Exception:
        return []


def _format_inbox(messages):
    lines = [
        "[maiko inbox] You have new message(s) — handle them before your next action:",
        "",
    ]
    for m in messages:
        sender = m.get("sender") or "user"
        mt = m.get("message_type") or "message"
        content = (m.get("content") or "").strip()
        lines.append(f"- [{sender} · {mt}] {content}")
    return "\n".join(lines)


def main():
    try:
        env_path = os.path.join(os.getcwd(), ".maiko-env.json")
        if not os.path.exists(env_path):
            return
        with open(env_path) as f:
            env = json.load(f)

        job_id = env.get("job_id") or env.get("task_id", "")
        agent_id = env.get("agent_id", "")
        api_url = env.get("api_url", "http://localhost:8420/api")
        if not job_id:
            return

        payload = json.loads(sys.stdin.read() or "{}")

        # Git event reporting (only on Bash matches that ran git commands).
        tool_input = payload.get("tool_input", {})
        command = tool_input.get("command", "")
        if command:
            try:
                _report_git_event(job_id, agent_id, api_url, command)
            except Exception:
                pass

        # Inbox poll — fires on every PostToolUse regardless of tool.
        # If there's anything unread, block-with-reason so the agent
        # processes it before continuing. The previous action still
        # stands; the block only stops the NEXT action until the agent
        # has read the message.
        messages = _poll_inbox(job_id, api_url)
        if messages:
            print(json.dumps({
                "decision": "block",
                "reason": _format_inbox(messages),
            }))

    except Exception:
        pass  # Non-blocking: silent on failure


if __name__ == "__main__":
    main()
