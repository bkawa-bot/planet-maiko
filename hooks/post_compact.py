#!/usr/bin/env python3
"""Claude Code hook: PostCompact.

Fires after context compaction. Fetches a fresh learning brief from the
Maiko API and sends it to the agent's inbox as a context_refresh message,
so the agent picks it up on the next inbox check.

Reads identity from .maiko-env.json in the current working directory.
Uses only stdlib. Non-blocking: 5s timeout, silent on failure.
"""

import json
import os
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

        task_id = env.get("task_id", "")
        agent_id = env.get("agent_id", "")
        api_url = env.get("api_url", "http://localhost:8420/api")

        if not task_id:
            return

        # Fetch fresh learning brief
        req = urllib.request.Request(
            f"{api_url}/learnings/brief",
            method="GET",
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        brief = data.get("brief", "")

        if not brief or brief == "No active learnings yet.":
            return

        # Send the brief as a context_refresh inbox message
        body = json.dumps({
            "sender": "maiko",
            "content": f"Context refreshed after compaction. Here are the current coding guidelines:\n\n{brief}",
            "message_type": "context_refresh",
        }).encode()

        req = urllib.request.Request(
            f"{api_url}/agents/{task_id}/inbox",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)

    except Exception:
        pass  # Non-blocking: silent on failure


if __name__ == "__main__":
    main()
