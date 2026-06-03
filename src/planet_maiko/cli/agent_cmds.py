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
        # The user decides when a task is complete (via the UI) or the
        # pr_merged automation does (on PR landing). Agents calling
        # /tasks/<id>/done would delete the task row outright. Use
        # `reply(message_type="ready_for_review")` from inside the
        # agent session instead.
        print(
            "Refused: agents don't close tasks. Use "
            "reply(message_type='ready_for_review') and let the user "
            "close after reviewing.",
            file=sys.stderr,
        )
        sys.exit(2)
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
    """Send a message back to Planet Maiko.

    Mirrors the MCP `reply` tool: same endpoint, same payload shape.
    Lets non-Claude runtimes (Aider, Codex, local Ollama loops, plain
    shell scripts) talk back through the same outbox without needing
    an MCP server in the worktree.
    """
    task_id = args.task or detect_task_id()
    if not task_id:
        print("Error: No job ID provided and could not detect from TASK.md or MAIKO_JOB_ID env", file=sys.stderr)
        sys.exit(1)

    data = {
        "content": args.message,
        "message_type": args.type,
        "sender": "agent",
        # Only "user" is a meaningful recipient today (it surfaces the
        # message in the user's memos). Anything else lands as in-thread
        # chatter the user picks up by opening the job page.
        "recipient": args.recipient or None,
    }
    api_request(f"/agents/{task_id}/outbox", method="POST", data=data)
    print(f"Sent reply for {task_id}")


def cmd_emit(args):
    """Post a structured output (type + content) to the agent's job so the
    workflow engine can read it without parsing free-text. Distinct from
    `reply` (the chat/artifact); this writes to agent_jobs.outputs. Used by
    e.g. the decomposer: one `emit --type task` per task it produces."""
    task_id = args.task or detect_task_id()
    if not task_id:
        print("Error: No job ID provided and could not detect from TASK.md or MAIKO_JOB_ID env", file=sys.stderr)
        sys.exit(1)
    data = {"type": args.type, "content": args.content}
    if getattr(args, "title", None):
        data["title"] = args.title
    if getattr(args, "repo", None):
        data["repo"] = args.repo
    api_request(f"/agents/{task_id}/outputs", method="POST", data=data)
    print(f"Emitted {args.type} output for {task_id}")


def cmd_request_changes(args):
    """Ask the workflow to send this loop's target back for another round,
    carrying your feedback. A loop's source node (e.g. a reviewer) calls
    this to fire the graph's loop edge. Generic loop control — not tied to a
    role or an output type. A no-op outside a flow that has a loop edge."""
    task_id = args.task or detect_task_id()
    if not task_id:
        print("Error: No job ID provided and could not detect from TASK.md or MAIKO_JOB_ID env", file=sys.stderr)
        sys.exit(1)
    api_request(
        f"/agents/{task_id}/request-changes",
        method="POST", data={"feedback": args.feedback},
    )
    print(f"Requested another round for {task_id}")


def cmd_check_code(args):
    """Run the mechanical checks for the agent's worktree.

    Mirrors the MCP `check_code` tool. Auto-detects tests / lint /
    typecheck from the repo (pyproject.toml, package.json, Cargo.toml,
    go.mod) or reads commands from .maiko/checks.json. The agent calls
    this BEFORE declaring ready_for_review to honor the protocol's
    "don't claim done if checks are red" rule.
    """
    task_id = args.task or detect_task_id()
    if not task_id:
        print("Error: No job ID provided and could not detect from TASK.md or MAIKO_JOB_ID env", file=sys.stderr)
        sys.exit(1)

    payload = {"job_id": task_id, "timeout": args.timeout}
    data = api_request("/checks/run", method="POST", data=payload)
    checks = data.get("checks") or []
    summary = data.get("summary") or {}

    if not checks:
        print(
            "Mechanical checks: none detected. Add a `.maiko/checks.json` "
            "with the commands you want agents to run, or ensure the repo "
            "has pyproject.toml + tests/, package.json with a test script, "
            "Cargo.toml, or go.mod."
        )
        return

    passed = summary.get("passed", 0)
    total = summary.get("total", 0)
    print(f"Mechanical checks: {passed}/{total} passed.")
    for c in checks:
        status = c.get("status", "?")
        mark = "OK " if status == "pass" else "FAIL" if status == "fail" else "?"
        bits = [status]
        if c.get("exit_code") is not None:
            bits.append(f"exit={c['exit_code']}")
        print(f"  [{mark}] {c.get('name', '?')} ({', '.join(bits)})")
        tail = c.get("output_tail") or ""
        if status != "pass" and tail:
            for line in tail.split("\n"):
                print(f"      {line}")

    if summary.get("blocked"):
        print("")
        print("Do NOT declare ready_for_review yet — address the failures first, then re-run.")
        sys.exit(1)


def cmd_session_report(args):
    """Report the agent's underlying session ID to Maiko.

    Tells Maiko "this job_id is now running under this session_id,"
    so the View Session link in the UI knows where to find the
    transcript file on disk. The agent (or a SessionStart hook)
    calls this once per spawn.

    Session ID defaults to $CLAUDE_SESSION_ID. Claude Code sets that
    env var before spawning any hooks or child processes. Pass
    --session-id explicitly for other runtimes.
    """
    import os as _os

    task_id = args.task or detect_task_id()
    if not task_id:
        print("Error: No job ID provided and could not detect from TASK.md or MAIKO_JOB_ID env", file=sys.stderr)
        sys.exit(1)

    session_id = args.session_id or _os.environ.get("CLAUDE_SESSION_ID") or ""
    if not session_id.strip():
        print("Error: No session ID — pass --session-id or set CLAUDE_SESSION_ID", file=sys.stderr)
        sys.exit(1)

    api_request(
        f"/agents/{task_id}/session",
        method="POST",
        data={"session_id": session_id.strip()},
    )
    print(f"Session {session_id.strip()} reported for {task_id}")


def cmd_leave_comment(args):
    """Pin an inline comment to a specific diff line.

    Mirrors the MCP `leave_comment` tool. Use sparingly (~5 max per
    review round) on lines that are uncertain, load-bearing, or
    deserve a second pair of eyes. Comments appear in the Review Diff
    page alongside the user's own comments but styled distinctly.
    """
    task_id = args.task or detect_task_id()
    if not task_id:
        print("Error: No job ID provided and could not detect from TASK.md or MAIKO_JOB_ID env", file=sys.stderr)
        sys.exit(1)

    # Body can come from the positional arg or from stdin so the agent
    # can pipe long markdown without escaping.
    body = args.body
    if body == "-" or body is None:
        body = sys.stdin.read()
    if not body or not body.strip():
        print("Error: Empty comment body — pass as positional arg or pipe via stdin", file=sys.stderr)
        sys.exit(1)

    data = {
        "file_path": args.file,
        "line_number": args.line,
        "side": args.side,
        "body": body,
    }
    api_request(f"/jobs/{task_id}/comments/agent", method="POST", data=data)
    print(f"Comment pinned to {args.file}:{args.line}")


def cmd_handoff(args):
    """Switch the current job's kind (investigation → coding, etc.).

    Useful when the agent's investigation surfaces work that wants
    coding, or a coding job uncovers a question that wants
    investigation. Updates the job + linked task on the backend and
    prints the new role's agent protocol so the running agent can
    adopt the new instructions mid-session without restarting.
    """
    task_id = args.task or detect_task_id()
    if not task_id:
        print(
            "Error: No job ID provided and could not detect from "
            "TASK.md or MAIKO_JOB_ID env",
            file=sys.stderr,
        )
        sys.exit(1)

    result = api_request(
        f"/agent-jobs/{task_id}/change-kind",
        method="POST",
        data={"kind": args.kind},
    )

    prev = result.get("previous_kind") or "?"
    print(f"Job kind: {prev} -> {args.kind}")
    protocol = result.get("protocol") or ""
    if not protocol.strip():
        print(
            f"(no protocol file found for {args.kind}; "
            "operate from the base agent contract)",
        )
        return
    print()
    print("=" * 64)
    print(f"NEW {args.kind.upper()} PROTOCOL")
    print("=" * 64)
    print(protocol)


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
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected from TASK.md if omitted; --task accepted for back-compat)")
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
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected if omitted; --task accepted for back-compat)")
    p.add_argument("--all", action="store_true", help="Show all messages, not just unread")
    p.set_defaults(func=cmd_inbox)

    # maiko reply
    p = subparsers.add_parser("reply", help="Send a message back to Planet Maiko")
    p.add_argument("message", help="Reply message")
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected from MAIKO_JOB_ID env or TASK.md if omitted; --task accepted for back-compat)")
    p.add_argument(
        "--type",
        default="message",
        choices=[
            "message", "status", "feedback", "insight", "stuck",
            "ready_for_review", "plan_for_approval", "pr_opened",
        ],
        help="Message type (matches the MCP reply tool's enum)",
    )
    p.add_argument(
        "--recipient",
        choices=["user"],
        help="Set to 'user' to surface this message in the user's memos. Leave unset for in-thread chatter.",
    )
    p.set_defaults(func=cmd_reply)

    # maiko emit — post a structured output the workflow engine reads
    p = subparsers.add_parser("emit", help="Post a structured output (task/plan/verdict/...) for the workflow engine")
    p.add_argument("content", help="Output content (use a heredoc for multi-line)")
    p.add_argument("--type", required=True, help="Output type: task | plan | diff | report | insight | proposal | verdict | comment")
    p.add_argument("--title", help="Short title — names the job spawned from this output (else a generic role label)")
    p.add_argument("--repo", help="Repo (org/name) this output's work targets — overrides the run's repo; omit to inherit it")
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected from MAIKO_JOB_ID env or TASK.md if omitted)")
    p.set_defaults(func=cmd_emit)

    p = subparsers.add_parser("request-changes", help="Ask the flow to loop this step's target back for another round")
    p.add_argument("feedback", help="What the target should change (use a heredoc for multi-line)")
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected from MAIKO_JOB_ID env or TASK.md if omitted)")
    p.set_defaults(func=cmd_request_changes)

    # maiko session-report — link CLAUDE_SESSION_ID to the AgentJob
    p = subparsers.add_parser("session-report", help="Report the agent's session ID to Maiko (replaces the MCP startup ping)")
    p.add_argument("--session-id", help="Session ID (defaults to $CLAUDE_SESSION_ID)")
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected from MAIKO_JOB_ID env or TASK.md if omitted)")
    p.set_defaults(func=cmd_session_report)

    # maiko check-code — mechanical-checks gate before ready_for_review
    p = subparsers.add_parser("check-code", help="Run mechanical checks (tests / lint / typecheck) on the worktree")
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected from MAIKO_JOB_ID env or TASK.md if omitted)")
    p.add_argument("--timeout", type=int, default=120, help="Per-check timeout in seconds (default: 120)")
    p.set_defaults(func=cmd_check_code)

    # maiko leave-comment — inline review comment
    p = subparsers.add_parser("leave-comment", help="Pin an inline comment to a diff line")
    p.add_argument("file", help="Path from the repo root (same as in the diff)")
    p.add_argument("line", type=int, help="Line number in the file")
    p.add_argument(
        "body",
        nargs="?",
        help="Comment body (markdown supported). Use '-' or omit to read from stdin.",
    )
    p.add_argument("--side", choices=["old", "new"], default="new", help="Which side of the diff (default: new)")
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected from MAIKO_JOB_ID env or TASK.md if omitted)")
    p.set_defaults(func=cmd_leave_comment)

    # maiko handoff — switch the current job's kind (and get the new role's protocol)
    p = subparsers.add_parser(
        "handoff",
        help="Switch this job's kind (coding / investigation / review / cartograph / repo_analysis) and print the new role's protocol.",
    )
    p.add_argument(
        "kind",
        choices=["coding", "investigation", "review", "cartograph", "repo_analysis"],
        help="Target kind to hand off to.",
    )
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected if omitted)")
    p.set_defaults(func=cmd_handoff)

    # maiko feedback
    p = subparsers.add_parser("feedback", help="Send in-session feedback about agent work")
    p.add_argument("message", help="Feedback message")
    p.add_argument("--category", default="pattern", help="Category: testing, security, error_handling, etc.")
    p.add_argument("--severity", default="suggestion", help="suggestion, warning, or blocking")
    p.add_argument("--code", help="Code snippet showing the pattern (before/after)")
    p.add_argument("--file", help="File path to include as code context")
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected if in worktree; --task accepted for back-compat)")
    p.set_defaults(func=cmd_feedback)

    # maiko sleep
    p = subparsers.add_parser("sleep", help="Put agent to sleep")
    p.add_argument("--job", "--task", dest="task", help="Job ID (auto-detected from TASK.md if omitted; --task accepted for back-compat)")
    p.set_defaults(func=cmd_sleep)

    # maiko wake
    p = subparsers.add_parser("wake", help="Wake a sleeping agent")
    p.add_argument("agent_id", help="Agent ID to wake")
    p.set_defaults(func=cmd_wake)
