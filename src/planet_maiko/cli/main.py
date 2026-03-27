#!/usr/bin/env python3
"""maiko CLI - communicate with Planet Maiko from anywhere.

Used by agents (and humans) to report status back to Planet Maiko.

Usage:
    maiko report "Status message here"
    maiko task done [task-id]
    maiko task start [task-id]
    maiko pupdate create --title "Title" --body "Body" --priority normal
    maiko status
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

import os
MAIKO_API = os.environ.get("MAIKO_API", "http://localhost:8420/api")


def api_request(path, method="GET", data=None):
    """Make a request to the Planet Maiko API."""
    url = f"{MAIKO_API}{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"} if data else {}

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"Error: Could not connect to Planet Maiko at {MAIKO_API}", file=sys.stderr)
        print(f"  Is the server running? (python3 app.py)", file=sys.stderr)
        sys.exit(1)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Error: {e.code} - {body}", file=sys.stderr)
        sys.exit(1)


def _detect_task_id():
    """Try to detect the task ID from the current directory's TASK.md."""
    try:
        with open("TASK.md") as f:
            for line in f:
                if line.startswith("**Task ID:**"):
                    return line.split("**Task ID:**")[1].strip()
    except FileNotFoundError:
        pass
    return None


def cmd_report(args):
    """Report a status message to Planet Maiko."""
    task_id = args.task or _detect_task_id()
    tags = [task_id] if task_id else []

    pupdate = {
        "id": f"agent-report-{int(time.time())}",
        "source": "agent",
        "type": args.type,
        "priority": args.priority,
        "title": f"[Agent] {args.message}",
        "body": args.body or "",
        "tags": tags,
    }

    result = api_request("/pupdates", method="POST", data=pupdate)
    print(f"Reported: {args.message}")
    if task_id:
        print(f"  Task: {task_id}")


def cmd_task(args):
    """Update a task's status."""
    task_id = args.task_id or _detect_task_id()
    if not task_id:
        print("Error: No task ID provided and could not detect from TASK.md", file=sys.stderr)
        sys.exit(1)

    action = args.action
    if action == "done":
        # Report completion via pupdate so the monitor picks it up
        pupdate = {
            "id": f"agent-done-{task_id}-{int(time.time())}",
            "source": "agent",
            "type": "agent_done",
            "priority": "normal",
            "title": f"[Agent] Task completed: {task_id}",
            "body": args.message or "Task completed successfully.",
            "tags": [task_id],
        }
        api_request("/pupdates", method="POST", data=pupdate)
        api_request(f"/tasks/{task_id}/done", method="POST")
        print(f"Task {task_id} marked as done")
    elif action == "start":
        api_request(f"/tasks/{task_id}/start", method="POST")
        print(f"Task {task_id} marked as in progress")
    elif action == "stuck":
        pupdate = {
            "id": f"agent-stuck-{task_id}-{int(time.time())}",
            "source": "agent",
            "type": "agent_stuck",
            "priority": "high",
            "title": f"[Agent] Stuck on: {task_id}",
            "body": args.message or "Agent needs help.",
            "tags": [task_id],
        }
        api_request("/pupdates", method="POST", data=pupdate)
        print(f"Reported stuck on {task_id}")


def cmd_inbox(args):
    """Check for messages from Planet Maiko."""
    task_id = args.task or _detect_task_id()
    if not task_id:
        print("Error: No task ID provided and could not detect from TASK.md", file=sys.stderr)
        sys.exit(1)

    params = "?unread_only=true&mark_read=true"
    if args.all:
        params = "?unread_only=false&mark_read=false"

    messages = api_request(f"/agents/{task_id}/inbox{params}")

    if not messages:
        print("No new messages.")
        return

    for msg in messages:
        sender = msg["sender"]
        mtype = msg["message_type"]
        time = msg["created_at"][:16].replace("T", " ")
        content = msg["content"]
        print(f"[{time}] ({sender}/{mtype}) {content}")


def cmd_reply(args):
    """Send a message back to Planet Maiko."""
    task_id = args.task or _detect_task_id()
    if not task_id:
        print("Error: No task ID provided and could not detect from TASK.md", file=sys.stderr)
        sys.exit(1)

    data = {
        "content": args.message,
        "message_type": args.type,
    }
    api_request(f"/agents/{task_id}/outbox", method="POST", data=data)
    print(f"Sent reply for {task_id}")


def cmd_status(args):
    """Check Planet Maiko's status."""
    brain = api_request("/brain/status")
    session = api_request("/brain/session")

    print(f"Brain cycles: {brain['cycle_count']}")
    print(f"Last cycle:   {brain['last_cycle'] or 'Never'}")
    print(f"Runtime:      {session['runtime']['name']}")
    print(f"Available:    {session['available']}")


def cmd_serve(args):
    """Start the Planet Maiko server."""
    from planet_maiko.app import create_app
    print(f"Starting Planet Maiko on http://{args.host}:{args.port}")
    app = create_app(start_scheduler=True)
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


def main():
    parser = argparse.ArgumentParser(
        prog="maiko",
        description="Planet Maiko - Personal engineering intelligence dashboard",
    )
    subparsers = parser.add_subparsers(dest="command")

    # maiko report
    report_parser = subparsers.add_parser("report", help="Report status to Planet Maiko")
    report_parser.add_argument("message", help="Status message")
    report_parser.add_argument("--body", help="Detailed body text")
    report_parser.add_argument("--task", help="Task ID (auto-detected from TASK.md if omitted)")
    report_parser.add_argument("--priority", default="normal", choices=["low", "normal", "high", "urgent"])
    report_parser.add_argument("--type", default="agent_update", help="Pupdate type")
    report_parser.set_defaults(func=cmd_report)

    # maiko task
    task_parser = subparsers.add_parser("task", help="Update task status")
    task_parser.add_argument("action", choices=["done", "start", "stuck"])
    task_parser.add_argument("task_id", nargs="?", help="Task ID (auto-detected if omitted)")
    task_parser.add_argument("--message", "-m", help="Optional message")
    task_parser.set_defaults(func=cmd_task)

    # maiko inbox
    inbox_parser = subparsers.add_parser("inbox", help="Check for messages from Planet Maiko")
    inbox_parser.add_argument("--task", help="Task ID (auto-detected if omitted)")
    inbox_parser.add_argument("--all", action="store_true", help="Show all messages, not just unread")
    inbox_parser.set_defaults(func=cmd_inbox)

    # maiko reply
    reply_parser = subparsers.add_parser("reply", help="Send a message back to Planet Maiko")
    reply_parser.add_argument("message", help="Reply message")
    reply_parser.add_argument("--task", help="Task ID (auto-detected if omitted)")
    reply_parser.add_argument("--type", default="message", help="Message type")
    reply_parser.set_defaults(func=cmd_reply)

    # maiko status
    status_parser = subparsers.add_parser("status", help="Check Planet Maiko status")
    status_parser.set_defaults(func=cmd_status)

    # maiko serve
    serve_parser = subparsers.add_parser("serve", help="Start Planet Maiko server")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    serve_parser.add_argument("--port", type=int, default=8420, help="Port to listen on")
    serve_parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    serve_parser.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
