"""Shared helpers for the maiko CLI submodules.

Holds the API client wrapper and the TASK.md task-id detector that
several commands need. Kept private (`_helpers`) so it doesn't get
imported as a public API.
"""

import json
import sys
import urllib.error
import urllib.request

from planet_maiko.config import maiko_api_url

MAIKO_API = maiko_api_url()


def api_request(path, method="GET", data=None):
    """Make a request to the Planet Maiko API.

    Calls sys.exit(1) on connection or HTTP errors with a friendly
    message — CLI commands generally don't want to handle these.
    """
    url = f"{MAIKO_API}{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Error: {e.code} - {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError:
        print(f"Error: Could not connect to Planet Maiko at {MAIKO_API}", file=sys.stderr)
        print(f"  Is the server running? (maiko serve)", file=sys.stderr)
        sys.exit(1)


def detect_task_id():
    """Try to detect the task ID from the current directory's TASK.md."""
    try:
        with open("TASK.md") as f:
            for line in f:
                if line.startswith("**Task ID:**"):
                    return line.split("**Task ID:**")[1].strip()
    except FileNotFoundError:
        pass
    return None
