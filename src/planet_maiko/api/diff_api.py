"""Diff review endpoints — diff rendering, inline comments, and the
request-changes / approve actions that drive the autonomous coding
agent review loop.

Shape of the flow:
  1. Coding agent works headlessly in a worktree, commits, and sends a
     `ready_for_review` message. A pupdate lands in the inbox.
  2. The user opens the review page, fetches GET /tasks/<id>/diff and
     GET /tasks/<id>/comments, drafts comments, then either:
       - POST /tasks/<id>/review/request-changes → comments are posted
         to the agent's inbox and the agent is woken up via a headless
         `claude --resume` thread; agent iterates, commits, sends a
         fresh ready_for_review.
       - POST /tasks/<id>/review/approve → branch is pushed, `gh pr
         create` runs, task is marked done, worktree cleaned up.

Agent-authored comments (from the leave_comment MCP tool) come in on
POST /tasks/<id>/comments/agent.
"""

import logging
import os
import subprocess
import threading
import uuid as _uuid

from flask import Blueprint, current_app, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.diff_comment import DiffComment
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

diff_bp = Blueprint("diff", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_or_404(task_id):
    task = db.session.get(Task, task_id)
    if not task:
        return None, (jsonify({"error": f"Task {task_id} not found"}), 404)
    return task, None


def _worktree_path(task):
    """Resolve the agent's working dir from task.extra.working_path.

    Returns None if unset or the path no longer exists — most calls
    should surface that as a 400 since the review UI is meaningless
    without a worktree.
    """
    wp = (task.extra or {}).get("working_path")
    if wp and os.path.isdir(wp):
        return wp
    return None


def _default_branch(worktree_path):
    """Resolve the default branch (main / master / trunk) of the parent
    repo so `git diff <base>..HEAD` compares against the right thing.
    Falls back to "main" when we can't tell — picking wrong here just
    means the diff includes extra history.
    """
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
            cwd=worktree_path, capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Output looks like "origin/main"
            return result.stdout.strip().split("/", 1)[-1]
    except Exception:
        pass
    return "main"


def _git(args, cwd, timeout=30):
    """Run a git command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# GET diff
# ---------------------------------------------------------------------------

@diff_bp.route("/tasks/<task_id>/diff", methods=["GET"])
def get_task_diff(task_id):
    """Return the unified diff of the agent's worktree vs the default branch."""
    task, err = _task_or_404(task_id)
    if err:
        return err
    worktree = _worktree_path(task)
    if not worktree:
        return jsonify({"error": "No worktree for this task (yet)"}), 400

    base_branch = _default_branch(worktree)
    rc, merge_base, merr = _git(["merge-base", "HEAD", f"origin/{base_branch}"], cwd=worktree)
    if rc != 0:
        # Fall back to the local base branch if origin ref is missing
        rc, merge_base, merr = _git(["merge-base", "HEAD", base_branch], cwd=worktree)
    base_sha = merge_base.strip() if rc == 0 else base_branch

    rc, head_sha, _ = _git(["rev-parse", "HEAD"], cwd=worktree)
    head_sha = head_sha.strip() if rc == 0 else ""

    rc, raw, err_out = _git(
        ["diff", "--no-color", f"{base_sha}..HEAD"],
        cwd=worktree, timeout=60,
    )
    if rc != 0:
        return jsonify({"error": f"git diff failed: {err_out.strip()[:200]}"}), 500

    return jsonify({
        "task_id": task_id,
        "base_branch": base_branch,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "raw_diff": raw,
    })


# ---------------------------------------------------------------------------
# Comment CRUD
# ---------------------------------------------------------------------------

@diff_bp.route("/tasks/<task_id>/comments", methods=["GET"])
def list_comments(task_id):
    """Return all comments for a task ordered by file, then line, then time."""
    comments = (
        DiffComment.query
        .filter_by(task_id=task_id)
        .order_by(
            DiffComment.file_path.asc(),
            DiffComment.line_number.asc(),
            DiffComment.created_at.asc(),
        )
        .all()
    )
    return jsonify([c.to_dict() for c in comments])


@diff_bp.route("/tasks/<task_id>/comments", methods=["POST"])
def create_comment(task_id):
    """Create a comment. Default status=draft, author=user."""
    data = request.get_json() or {}
    if not data.get("body"):
        return jsonify({"error": "body is required"}), 400
    if "file_path" not in data or "line_number" not in data:
        return jsonify({"error": "file_path and line_number are required"}), 400

    # Validate parent_id belongs to the same task if provided
    parent_id = data.get("parent_id")
    if parent_id:
        parent = db.session.get(DiffComment, parent_id)
        if not parent or parent.task_id != task_id:
            return jsonify({"error": "parent_id does not belong to this task"}), 400

    comment = DiffComment(
        task_id=task_id,
        file_path=data["file_path"],
        line_number=int(data["line_number"]),
        side=data.get("side", "new"),
        base_sha=data.get("base_sha"),
        body=data["body"],
        parent_id=parent_id,
        status=data.get("status", "draft"),
        author=data.get("author", "user"),
    )
    db.session.add(comment)
    db.session.commit()
    return jsonify(comment.to_dict()), 201


@diff_bp.route("/comments/<int:comment_id>", methods=["PATCH"])
def update_comment(comment_id):
    """Edit body, toggle resolved, or submit a draft."""
    comment = db.session.get(DiffComment, comment_id)
    if not comment:
        return jsonify({"error": "Comment not found"}), 404
    data = request.get_json() or {}
    if "body" in data:
        comment.body = data["body"]
    if "status" in data:
        # Allow user-driven status changes: draft→submitted, any→resolved,
        # resolved→submitted (re-open). "outdated" is server-only.
        new_status = data["status"]
        if new_status in ("draft", "submitted", "resolved"):
            comment.status = new_status
    db.session.commit()
    return jsonify(comment.to_dict())


@diff_bp.route("/comments/<int:comment_id>", methods=["DELETE"])
def delete_comment(comment_id):
    """Delete a draft comment. Submitted comments stay for history."""
    comment = db.session.get(DiffComment, comment_id)
    if not comment:
        return jsonify({"error": "Comment not found"}), 404
    if comment.status != "draft":
        return jsonify({"error": "Only draft comments can be deleted"}), 400
    db.session.delete(comment)
    db.session.commit()
    return jsonify({"deleted": comment_id})


# ---------------------------------------------------------------------------
# Agent-authored comment (internal — called by the MCP channel)
# ---------------------------------------------------------------------------

@diff_bp.route("/tasks/<task_id>/comments/agent", methods=["POST"])
def create_agent_comment(task_id):
    """Internal endpoint for the leave_comment MCP tool.

    Always stores as author=agent, status=submitted. The agent anchors
    comments to the user's post-image (the "new" side) by default; they
    can override by passing side="old" explicitly.
    """
    data = request.get_json() or {}
    if not data.get("body"):
        return jsonify({"error": "body is required"}), 400
    if "file_path" not in data or "line_number" not in data:
        return jsonify({"error": "file_path and line_number are required"}), 400
    comment = DiffComment(
        task_id=task_id,
        file_path=data["file_path"],
        line_number=int(data["line_number"]),
        side=data.get("side", "new"),
        base_sha=data.get("base_sha"),
        body=data["body"],
        status="submitted",
        author="agent",
    )
    db.session.add(comment)
    db.session.commit()
    return jsonify(comment.to_dict()), 201


# ---------------------------------------------------------------------------
# Review actions: request changes / approve
# ---------------------------------------------------------------------------

def _format_review_message(comments):
    """Compose the markdown blob the agent receives via its inbox.

    Each comment becomes `@@ <file>:<line>` + a quoted body so the
    agent can parse the anchors deterministically.
    """
    lines = [
        "The user reviewed your diff and left the following comments.",
        "Address each one, commit the changes, and reply with",
        "message_type=\"ready_for_review\" when you're ready for another",
        "review round.",
        "",
    ]
    for c in comments:
        lines.append(f"@@ {c.file_path}:{c.line_number} ({c.side})")
        for body_line in c.body.splitlines() or [""]:
            lines.append(f"> {body_line}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _resume_agent_with_review(task_id, working_path):
    """Fire a daemon thread that runs `claude --resume <session>
    --print "Check your inbox for review feedback"`.

    The agent wakes up, calls check_inbox, processes the review
    message the request-changes handler just stored, iterates, and
    sends a new ready_for_review message. Same pattern the review /
    investigation agents already use — sandboxed to the worktree,
    no human needed.
    """
    import shutil
    claude_path = shutil.which("claude")
    if not claude_path:
        logger.warning("[diff] claude CLI not found; agent won't auto-resume")
        return False
    from planet_maiko.api.agents_api import _get_sessions
    info = _get_sessions().get(task_id)
    if not info or not info.get("session_id"):
        logger.warning(f"[diff] No session registered for task {task_id}; can't resume")
        return False
    session_id = info["session_id"]

    cmd = [
        claude_path, "--print", "--output-format", "text",
        "--resume", session_id,
        "--dangerously-skip-permissions",
    ]

    def _run():
        try:
            log_path = os.path.join(working_path, "agent.log")
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"\n\n# Resumed for review at {_uuid.uuid4().hex[:8]}\n\n")
                log.flush()
                subprocess.run(
                    cmd,
                    input="You have new review feedback. Call check_inbox to read it, address each comment, commit, and reply with message_type=\"ready_for_review\" when done.",
                    stdout=log, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    cwd=working_path,
                )
        except Exception as e:
            logger.warning(f"[diff] Resume for {task_id} failed: {e}")

    threading.Thread(target=_run, daemon=True, name=f"review-{task_id}").start()
    return True


@diff_bp.route("/tasks/<task_id>/review/request-changes", methods=["POST"])
def request_changes(task_id):
    """Submit all draft comments and wake the agent up to iterate."""
    task, err = _task_or_404(task_id)
    if err:
        return err
    worktree = _worktree_path(task)
    if not worktree:
        return jsonify({"error": "No worktree for this task"}), 400

    drafts = (
        DiffComment.query
        .filter_by(task_id=task_id, status="draft", author="user")
        .order_by(DiffComment.file_path.asc(), DiffComment.line_number.asc())
        .all()
    )
    if not drafts:
        return jsonify({"error": "No draft comments to submit"}), 400

    for c in drafts:
        c.status = "submitted"
    db.session.commit()

    # Post a single review message into the agent's inbox so it shows
    # up as one cohesive round on check_inbox, not N scattered ones.
    from planet_maiko.models.agent_message import AgentMessage
    review_body = _format_review_message(drafts)
    inbox_msg = AgentMessage(
        task_id=task_id,
        direction="to_agent",
        sender="user",
        content=review_body,
        message_type="review",
    )
    db.session.add(inbox_msg)
    db.session.commit()

    # Harvest each comment as a corrective-VIOLATION training pair.
    # The user's comment body is the violation the model should have
    # caught; we pull a small code window from the worktree file so
    # the pair has the code context the LoRA trains on. Best-effort —
    # failures here don't block the review flow.
    try:
        repo = (task.extra or {}).get("repo") or (task.extra or {}).get("repository")
        _harvest_comments_as_training_pairs(drafts, worktree, repo)
    except Exception as e:
        logger.warning(f"[harvest] Local comment harvest failed for {task_id}: {e}")

    resumed = _resume_agent_with_review(task_id, worktree)

    return jsonify({
        "submitted_comments": len(drafts),
        "agent_resumed": resumed,
    })


def _harvest_comments_as_training_pairs(comments, worktree, repo):
    """Record each submitted comment as a corrective-VIOLATION pair.

    The comment's body is the description of what the reviewer wanted
    fixed — same grammar as the LoRA's training labels. The code
    snippet is a +/-3 line window from the worktree file at the
    comment's line. Categories are left to the LLM classifier
    downstream since reviewers don't write in categories.
    """
    from planet_maiko.brain.learning.feedback import add_corrective_violation
    for c in comments:
        code_snippet = _extract_code_window(worktree, c.file_path, c.line_number)
        if not code_snippet:
            continue
        try:
            add_corrective_violation(
                code=code_snippet,
                violation=c.body,
                category=None,  # classifier picks it up
                file_path=c.file_path,
                repo=repo,
            )
        except Exception as e:
            logger.debug(f"[harvest] Failed recording comment {c.id}: {e}")


def _extract_code_window(worktree, file_path, line_number, window=3):
    """Return ~(window*2+1) lines around line_number from the file in worktree.

    Returns None if the file can't be read. Keeps things best-effort —
    training-pair harvest shouldn't hard-fail on a missing file.
    """
    full_path = os.path.join(worktree, file_path)
    if not os.path.isfile(full_path):
        return None
    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return None
    if not lines:
        return None
    start = max(0, line_number - 1 - window)
    end = min(len(lines), line_number + window)
    return "".join(lines[start:end])


def _build_pr_body(task, comments):
    """Construct the PR description posted to GitHub on approve.

    Includes the task title, any source description, and — critically
    — a resolved-comments summary so reviewers on GitHub can see what
    rounds of feedback happened locally before the PR was opened.
    """
    lines = [f"## {task.title}", ""]
    extra = task.extra or {}
    if extra.get("description"):
        lines.append(extra["description"])
        lines.append("")
    lines.append(f"Task ID: `{task.id}`")
    lines.append("")
    if comments:
        lines.append("## Review rounds")
        lines.append("")
        for c in comments:
            marker = "✓" if c.status == "resolved" else "·"
            lines.append(f"- {marker} `{c.file_path}:{c.line_number}` — {c.body.splitlines()[0] if c.body else ''}")
        lines.append("")
    lines.append("_Opened by Planet Maiko after local review._")
    return "\n".join(lines)


@diff_bp.route("/tasks/<task_id>/plan", methods=["GET"])
def get_plan(task_id):
    """Return the latest plan_for_approval content the agent sent, plus
    the task's plan_approved_at timestamp so the UI can tell the user
    what state they're in.
    """
    task, err = _task_or_404(task_id)
    if err:
        return err
    from planet_maiko.models.agent_message import AgentMessage
    latest = (
        AgentMessage.query
        .filter_by(task_id=task_id, direction="from_agent", message_type="plan_for_approval")
        .order_by(AgentMessage.created_at.desc())
        .first()
    )
    extra = task.extra or {}
    return jsonify({
        "task_id": task_id,
        "plan_first": bool(extra.get("plan_first")),
        "plan_approved_at": extra.get("plan_approved_at"),
        "plan": latest.content if latest else None,
        "plan_at": latest.created_at.isoformat() if latest and latest.created_at else None,
    })


def _resume_for_plan(task_id, working_path, instruction, plan_mode):
    """Fire a daemon thread that resumes the agent with the given
    instruction. If plan_mode is True, re-applies --permission-mode
    plan so a requested revision stays read-only until another
    approval. Same pattern as _resume_agent_with_review.
    """
    import shutil
    import threading

    claude_path = shutil.which("claude")
    if not claude_path:
        return False
    from planet_maiko.api.agents_api import _get_sessions
    info = _get_sessions().get(task_id)
    if not info or not info.get("session_id"):
        return False
    session_id = info["session_id"]

    cmd = [
        claude_path, "--print", "--output-format", "text",
        "--resume", session_id,
        "--dangerously-skip-permissions",
    ]
    if plan_mode:
        cmd.extend(["--permission-mode", "plan"])

    def _run():
        try:
            log_path = os.path.join(working_path, "agent.log")
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(f"\n\n# Plan resume ({'revise' if plan_mode else 'approved'}) @ {datetime.now(timezone.utc).isoformat()}\n\n")
                log.flush()
                subprocess.run(
                    cmd, input=instruction,
                    stdout=log, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    cwd=working_path,
                )
        except Exception as e:
            logger.warning(f"[plan] Resume for {task_id} failed: {e}")

    threading.Thread(target=_run, daemon=True, name=f"plan-{task_id}").start()
    return True


@diff_bp.route("/tasks/<task_id>/plan/approve", methods=["POST"])
def approve_plan(task_id):
    """User approved the agent's proposed plan — resume without plan
    mode so the agent can actually write code now.
    """
    from planet_maiko.models.agent_message import AgentMessage

    task, err = _task_or_404(task_id)
    if err:
        return err
    worktree = _worktree_path(task)
    if not worktree:
        return jsonify({"error": "No worktree for this task"}), 400

    # Log the approval into the conversation so the transcript is
    # complete, then resume without plan mode.
    db.session.add(AgentMessage(
        task_id=task_id,
        direction="to_agent",
        sender="user",
        content="Plan approved. Go implement it — make the changes, commit locally, and call reply(message_type='ready_for_review') when done.",
        message_type="plan_approved",
    ))
    extra = dict(task.extra or {})
    extra["plan_approved_at"] = datetime.now(timezone.utc).isoformat()
    task.extra = extra
    db.session.commit()

    resumed = _resume_for_plan(
        task_id, worktree,
        instruction=(
            "Your plan was approved. Implement it now: follow the plan, "
            "commit your changes locally, and call "
            "reply(message_type='ready_for_review') when you're ready for "
            "the user to review the diff. Don't git push."
        ),
        plan_mode=False,
    )
    return jsonify({"task_id": task_id, "agent_resumed": resumed, "mode": "implementing"})


@diff_bp.route("/tasks/<task_id>/plan/revise", methods=["POST"])
def revise_plan(task_id):
    """User wants the agent to revise the plan before implementing."""
    from planet_maiko.models.agent_message import AgentMessage

    task, err = _task_or_404(task_id)
    if err:
        return err
    worktree = _worktree_path(task)
    if not worktree:
        return jsonify({"error": "No worktree for this task"}), 400

    data = request.get_json(silent=True) or {}
    feedback = (data.get("feedback") or "").strip()
    if not feedback:
        return jsonify({"error": "feedback is required"}), 400

    db.session.add(AgentMessage(
        task_id=task_id,
        direction="to_agent",
        sender="user",
        content=feedback,
        message_type="plan_revision",
    ))
    db.session.commit()

    resumed = _resume_for_plan(
        task_id, worktree,
        instruction=(
            "The user reviewed your plan and has feedback. Revise the "
            "plan based on their comments and call "
            "reply(message_type='plan_for_approval') with the updated "
            "version. Do NOT write code yet.\n\n"
            f"User feedback:\n{feedback}"
        ),
        plan_mode=True,
    )
    return jsonify({"task_id": task_id, "agent_resumed": resumed, "mode": "revising"})


@diff_bp.route("/tasks/<task_id>/review/approve", methods=["POST"])
def approve(task_id):
    """Push the branch to origin; open a PR if this is the first approval.

    Tasks stay open (status=in_review) across review rounds. The first
    approve runs `gh pr create` and stores pr_url on task.extra.
    Subsequent approves (after the agent iterates on reviewer
    comments) just push the updated commits to the same branch — the
    existing PR updates automatically. The task only closes when the
    PR actually merges (github_poller → _complete_review_task).

    Worktree stays around for the lifetime of the review cycle so the
    agent can resume into it on future pr_review_commented events.
    """
    import shutil
    from datetime import datetime, timezone

    task, err = _task_or_404(task_id)
    if err:
        return err
    worktree = _worktree_path(task)
    if not worktree:
        return jsonify({"error": "No worktree for this task"}), 400

    branch = (task.extra or {}).get("branch")
    if not branch:
        return jsonify({"error": "No branch tracked for this task"}), 400

    submitted = DiffComment.query.filter_by(task_id=task_id, status="submitted").all()
    for c in submitted:
        c.status = "resolved"
    all_comments = DiffComment.query.filter_by(task_id=task_id).all()

    existing_pr_url = (task.extra or {}).get("pr_url")

    rc, _, perr = _git(["push", "-u", "origin", branch], cwd=worktree, timeout=120)
    if rc != 0:
        db.session.commit()
        return jsonify({"error": f"git push failed: {perr.strip()[:300]}"}), 500

    pr_url = existing_pr_url
    pr_created = False
    gh_path = shutil.which("gh")
    # Only open a new PR if this is the first approve for this task.
    # Subsequent approves push updates to the existing branch; GitHub
    # reflects the new commits on the open PR automatically.
    if gh_path and not existing_pr_url:
        body = _build_pr_body(task, all_comments)
        try:
            result = subprocess.run(
                [gh_path, "pr", "create", "--title", task.title, "--body", body],
                cwd=worktree, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
            if result.returncode == 0:
                out = result.stdout.strip()
                pr_url = out.splitlines()[-1] if out else None
                pr_created = bool(pr_url)
            else:
                logger.warning(f"[diff] gh pr create failed: {result.stderr.strip()[:200]}")
        except Exception as e:
            logger.warning(f"[diff] gh pr create raised: {e}")

    # Task stays in_review until the PR merges — _complete_review_task
    # closes it when the github_poller sees pr_merged.
    task.status = "in_review"
    task.updated_at = datetime.now(timezone.utc)
    extra = dict(task.extra or {})
    if pr_url:
        task.url = pr_url
        extra["pr_url"] = pr_url
    extra["last_approved_at"] = datetime.now(timezone.utc).isoformat()
    task.extra = extra
    db.session.commit()

    return jsonify({
        "task_id": task_id,
        "branch": branch,
        "pr_url": pr_url,
        "pr_created": pr_created,
        "gh_installed": bool(gh_path),
    })
