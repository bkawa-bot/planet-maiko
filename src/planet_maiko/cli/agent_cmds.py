"""CLI commands used by agents (and humans) for inter-agent communication.

These commands talk to the running Planet Maiko server over HTTP. They
are designed to be cheap and quick — agents call them frequently from
within their worktree session.

Commands: report, task, inbox, reply, feedback, sleep, wake.
"""

import sys
import time

from planet_maiko.cli._helpers import api_request, detect_task_id


def cmd_report(args):
    """Report a status message to Planet Maiko."""
    task_id = args.task or detect_task_id()
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

    api_request("/pupdates", method="POST", data=pupdate)
    print(f"Reported: {args.message}")
    if task_id:
        print(f"  Task: {task_id}")


def cmd_task(args):
    """Update a task's status."""
    task_id = args.task_id or detect_task_id()
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
    task_id = args.task or detect_task_id()
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
        ts = msg["created_at"][:16].replace("T", " ")
        content = msg["content"]
        print(f"[{ts}] ({sender}/{mtype}) {content}")


def cmd_reply(args):
    """Send a message back to Planet Maiko."""
    task_id = args.task or detect_task_id()
    if not task_id:
        print("Error: No task ID provided and could not detect from TASK.md", file=sys.stderr)
        sys.exit(1)

    data = {
        "content": args.message,
        "message_type": args.type,
    }
    api_request(f"/agents/{task_id}/outbox", method="POST", data=data)
    print(f"Sent reply for {task_id}")


def cmd_feedback(args):
    """Send in-session feedback about agent work."""
    task_id = args.task or detect_task_id()
    if not task_id:
        print("Error: Could not detect task ID. Use --task to specify.")
        return

    # Create a Signal directly with code context if provided
    signal_data = {
        "category": args.category,
        "text": args.message,
        "source_type": "session_feedback",
        "severity": args.severity,
    }

    # Read code from --code flag or --file
    if args.code:
        signal_data["code_context"] = args.code
    elif args.file:
        try:
            with open(args.file) as f:
                signal_data["code_context"] = f.read()[:3000]
            signal_data["file_path"] = args.file
        except Exception:
            pass

    try:
        api_request("/signals", method="POST", data=signal_data)
    except SystemExit:
        pass  # Server might not be running — still send via outbox

    # Also send via agent outbox for dashboard visibility
    data = {
        "content": args.message,
        "message_type": "feedback",
        "sender": "agent",
        "metadata": {
            "feedback_category": args.category,
            "feedback_severity": args.severity,
        }
    }
    api_request(f"/agents/{task_id}/outbox", method="POST", data=data)
    print(f"Feedback recorded for {task_id} [{args.category}]")


def cmd_sleep(args):
    """Put an agent to sleep."""
    task_id = args.task or detect_task_id()
    if not task_id:
        print("Error: Could not detect task ID. Use --task.")
        return
    data = {"content": "Going to sleep.", "message_type": "agent_sleep", "sender": "agent"}
    api_request(f"/agents/{task_id}/outbox", method="POST", data=data)
    print(f"Agent sleeping. Wake with: maiko wake agent-{task_id}")


def cmd_wake(args):
    """Wake a sleeping agent."""
    agent_id = args.agent_id
    # Send a wake pupdate
    data = {
        "id": f"wake-{agent_id}-{int(time.time())}",
        "source": "maiko",
        "type": "agent_wake",
        "priority": "normal",
        "title": f"Wake up, {agent_id}!",
        "body": "Time to get back to work. Check your inbox for updates.",
        "tags": [agent_id, "wake"],
    }
    api_request("/pupdates", method="POST", data=data)
    print(f"Wake signal sent to {agent_id}")


def register(subparsers):
    """Register agent communication subcommands."""
    # maiko report
    p = subparsers.add_parser("report", help="Report status to Planet Maiko")
    p.add_argument("message", help="Status message")
    p.add_argument("--body", help="Detailed body text")
    p.add_argument("--task", help="Task ID (auto-detected from TASK.md if omitted)")
    p.add_argument("--priority", default="normal", choices=["low", "normal", "high", "urgent"])
    # Default to agent_status, NOT agent_update. agent_update is the
    # type the post-tool-use hook emits on every git commit / bash /
    # write — monitor.py treats those as noise so the activity feed's
    # speech bubble doesn't read "agent git commit" forever. Agent-
    # authored intentional status messages need a distinct type so
    # they actually surface; otherwise they get filtered out alongside
    # the tool spam and the dashboard shows the prepare-time "Agent
    # ready: ..." pupdate as the most recent non-noise message.
    p.add_argument("--type", default="agent_status", help="Pupdate type")
    p.set_defaults(func=cmd_report)

    # maiko task
    p = subparsers.add_parser("task", help="Update task status")
    p.add_argument("action", choices=["done", "start", "stuck"])
    p.add_argument("task_id", nargs="?", help="Task ID (auto-detected if omitted)")
    p.add_argument("--message", "-m", help="Optional message")
    p.set_defaults(func=cmd_task)

    # maiko inbox
    p = subparsers.add_parser("inbox", help="Check for messages from Planet Maiko")
    p.add_argument("--task", help="Task ID (auto-detected if omitted)")
    p.add_argument("--all", action="store_true", help="Show all messages, not just unread")
    p.set_defaults(func=cmd_inbox)

    # maiko reply
    p = subparsers.add_parser("reply", help="Send a message back to Planet Maiko")
    p.add_argument("message", help="Reply message")
    p.add_argument("--task", help="Task ID (auto-detected if omitted)")
    p.add_argument("--type", default="message", help="Message type")
    p.set_defaults(func=cmd_reply)

    # maiko feedback
    p = subparsers.add_parser("feedback", help="Send in-session feedback about agent work")
    p.add_argument("message", help="Feedback message")
    p.add_argument("--category", default="pattern", help="Category: testing, security, error_handling, etc.")
    p.add_argument("--severity", default="suggestion", help="suggestion, warning, or blocking")
    p.add_argument("--code", help="Code snippet showing the pattern (before/after)")
    p.add_argument("--file", help="File path to include as code context")
    p.add_argument("--task", help="Task ID (auto-detected if in worktree)")
    p.set_defaults(func=cmd_feedback)

    # maiko sleep
    p = subparsers.add_parser("sleep", help="Put agent to sleep")
    p.add_argument("--task", help="Task ID (auto-detected from TASK.md if omitted)")
    p.set_defaults(func=cmd_sleep)

    # maiko wake
    p = subparsers.add_parser("wake", help="Wake a sleeping agent")
    p.add_argument("agent_id", help="Agent ID to wake")
    p.set_defaults(func=cmd_wake)
