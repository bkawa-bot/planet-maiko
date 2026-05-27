#!/usr/bin/env python3
"""Claude Code hook: PostToolUse (matcher="*").

Fires after every tool use (the scaffold installs us with the
wildcard matcher). Does three things:

1. Sends a heartbeat to Maiko so the dashboard knows the agent is
   actively working even between status messages. The server-side
   endpoint bumps last_active_at and stashes the tool + hint for the
   "currently using <Tool> on <path>" UI. Server rate-limits to one
   accepted ping per 15s per job, so it's safe to fire on every tool.
2. Reports git commit / push events to Maiko so the dashboard stays
   in sync.
3. Polls the Maiko inbox and, if there are unread messages, surfaces
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


def _heartbeat(job_id, api_url, tool_name, tool_input):
    """Bump server-side last_active_at + last-tool-used hint.

    Cheap: server rate-limits per-job so calling this on every tool
    call is fine. Fire-and-forget; we don't care about the response.
    """
    try:
        hint = _summarize_tool(tool_name, tool_input)
        body = json.dumps({"tool": tool_name or "", "hint": hint}).encode()
        req = urllib.request.Request(
            f"{api_url}/agents/{job_id}/heartbeat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        # Best-effort. A failed heartbeat is invisible to the agent;
        # the server's nudge / stuck-check fall back to turn-boundary
        # last_active_at as before.
        pass


def _summarize_tool(tool_name, tool_input):
    """Short human-readable hint for the UI's "currently doing X" panel.

    Tool inputs vary by tool: Bash has command, Read/Edit/Write have
    file_path, Glob/Grep have pattern, Task has description /
    subagent_type. Just grab whichever field exists and truncate. None
    of this is correctness-critical — it's a UI label.
    """
    if not isinstance(tool_input, dict):
        return ""
    # In rough order of how informative each field is for a label.
    for key in ("command", "file_path", "path", "pattern", "description",
                "subagent_type", "query", "url"):
        v = tool_input.get(key)
        if v:
            s = str(v).strip().replace("\n", " ")
            return s[:200]
    return ""


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
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {})

        # Heartbeat fires on EVERY tool use (not just Bash) — the
        # server rate-limits per-job so it's safe to ping every call.
        # Lets the UI show "currently using <Tool>" and keeps the
        # nudge / stuck-check phases accurate during long turns.
        _heartbeat(job_id, api_url, tool_name, tool_input)

        # Git event reporting (only on Bash matches that ran git commands).
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
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
