#!/usr/bin/env python3
"""Claude Code hook: Notification.

Fires when Claude sends a notification (e.g. task complete, error).
Checks if the title/body contains milestone keywords and, if so,
creates a pupdate so the dashboard surfaces it.

Reads identity from .maiko-env.json in the current working directory.
Uses only stdlib. Non-blocking: 5s timeout, silent on failure.
"""

import json
import os
import sys
import urllib.request

MILESTONE_KEYWORDS = [
    "complete", "done", "finish", "pass", "fail",
    "error", "stuck", "pr", "merge",
]


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
        title = payload.get("title", "")
        body = payload.get("body", "")

        # Check for milestone keywords (case-insensitive)
        combined = (title + " " + body).lower()
        if not any(kw in combined for kw in MILESTONE_KEYWORDS):
            return

        req_body = json.dumps({
            "job_id": job_id,
            "agent_id": agent_id,
            "title": title,
            "body": body,
        }).encode()

        req = urllib.request.Request(
            f"{api_url}/hooks/notification",
            data=req_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)

    except Exception:
        pass  # Non-blocking: silent on failure


if __name__ == "__main__":
    main()
