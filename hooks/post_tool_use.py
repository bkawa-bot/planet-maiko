#!/usr/bin/env python3
"""Claude Code hook: PostToolUse (Bash matcher).

Fires after every Bash tool use. If the command contains "git commit" or
"git push", sends an event to the Maiko API so the dashboard stays in sync.

Reads identity from .maiko-env.json in the current working directory.
Uses only stdlib. Non-blocking: 5s timeout, silent on failure.
"""

import json
import os
import re
import sys
import urllib.request


def main():
    try:
        # Read identity from env file
        env_path = os.path.join(os.getcwd(), ".maiko-env.json")
        if not os.path.exists(env_path):
            return
        with open(env_path) as f:
            env = json.load(f)

        job_id = env.get("job_id") or env.get("task_id", "")
        agent_id = env.get("agent_id", "")
        api_url = env.get("api_url", "http://localhost:8420/api")

        # Read hook payload from stdin
        payload = json.loads(sys.stdin.read())
        tool_input = payload.get("tool_input", {})
        command = tool_input.get("command", "")

        if not command:
            return

        # Only fire on git commit or git push
        is_commit = "git commit" in command
        is_push = "git push" in command

        if not is_commit and not is_push:
            return

        # Extract commit message from -m flag if present
        message = ""
        if is_commit:
            # Match -m "msg", -m 'msg', or -m msg
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

    except Exception:
        pass  # Non-blocking: silent on failure


if __name__ == "__main__":
    main()
