#!/usr/bin/env python3
"""Claude Code hook: SubagentStop.

Fires when a subagent finishes. Creates a low-priority pupdate so the
dashboard knows a subtask completed.

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

        job_id = env.get("job_id") or env.get("task_id", "")
        agent_id = env.get("agent_id", "")
        api_url = env.get("api_url", "http://localhost:8420/api")

        # Read hook payload from stdin (subagent result info)
        payload = json.loads(sys.stdin.read())

        req_body = json.dumps({
            "job_id": job_id,
            "agent_id": agent_id,
        }).encode()

        req = urllib.request.Request(
            f"{api_url}/hooks/subagent-stop",
            data=req_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)

    except Exception:
        pass  # Non-blocking: silent on failure


if __name__ == "__main__":
    main()
