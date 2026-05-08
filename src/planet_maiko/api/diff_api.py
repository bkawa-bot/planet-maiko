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
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from planet_maiko.database import db, iso_utc
from planet_maiko.models.diff_comment import DiffComment
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

diff_bp = Blueprint("diff", __name__)


def _resolve_task_id(inbox_id):
    """Translate an inbox id → a stable id for diff_comments storage.

    Order of preference:
      1. Already a Task.id → use it.
      2. AgentJob.id with source_task_id set → use the linked Task.id
         so the comment lives in the Task's id space (review surface
         the user sees).
      3. AgentJob.id without source_task_id → use the job.id directly.
         The DiffComment.task_id column dropped its FK to tasks.id
         (see app._drop_diff_comment_task_fk) so this is allowed; the
         /tasks/<id>/comments endpoint will canonicalize back to the
         same id at read time.

    Always returns SOMETHING — callers used to 404 when no Task matched,
    which broke leave_comment for review agents whose job has no
    linked task.
    """
    if db.session.get(Task, inbox_id) is not None:
        return inbox_id
    from planet_maiko.models.agent_job import AgentJob
    job = db.session.get(AgentJob, inbox_id)
    if job is not None:
        return job.source_task_id or job.id
    return inbox_id


def _canonical_inbox_id(any_id):
    """Translate any URL id → the AgentJob.id the agent's MCP env
    actually points at, so AgentMessage rows we write are visible
    to the agent's check_inbox.

    Accepts either:
      - A Task.id  → looks up the most-recent non-cancelled linked job
      - A Job.id   → returns it as-is

    Falls back to the input id when nothing resolves so legacy
    task-keyed flows (pre-unification, no AgentJob row) still work.
    """
    from planet_maiko.models.agent_job import AgentJob
    # If the caller already handed us a Job.id, that IS the canonical
    # inbox id — no lookup needed.
    if db.session.get(AgentJob, any_id) is not None:
        return any_id
    job = (
        AgentJob.query
        .filter_by(source_task_id=any_id)
        .filter(AgentJob.status != "cancelled")
        .order_by(AgentJob.created_at.desc())
        .first()
    )
    return job.id if job is not None else any_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _task_or_404(task_id):
    """Resolve a request-id (URL param) to a Task row.

    Accepts either a Task.id directly OR an AgentJob.id whose
    source_task_id points at a Task. Post-unification the AgentJobPage
    passes jobId to every /tasks/<id>/* endpoint here; this lookup
    canonicalizes so the entire diff_api surface is id-space agnostic.

    Returns (task, None) on success, (None, error_response) on 404.
    """
    task = db.session.get(Task, task_id)
    if task is not None:
        return task, None
    from planet_maiko.models.agent_job import AgentJob
    job = db.session.get(AgentJob, task_id)
    if job is not None and job.source_task_id:
        linked = db.session.get(Task, job.source_task_id)
        if linked is not None:
            return linked, None
    return None, (jsonify({"error": f"Task {task_id} not found"}), 404)


def _worktree_path(task):
    """Resolve the agent's working dir for diff/review operations.

    Two storage spots, in priority order:
      1. task.extra.working_path — legacy task-keyed flow.
      2. AgentJob.worktree_path on the most recent non-cancelled job
         linked to this task — the unified path stores the worktree
         here, NOT on task.extra. Without this fallback, coding
         agents (which always run via the unified path post-Stage D)
         have a working diff on disk but the diff endpoint can't
         find it; the review page renders the agent's summary +
         verdict (those land on task.extra) but no actual diff.

    Returns None if both are unset or the path no longer exists.
    """
    wp = (task.extra or {}).get("working_path")
    if wp and os.path.isdir(wp):
        return wp
    try:
        from planet_maiko.models.agent_job import AgentJob
        job = (
            AgentJob.query
            .filter_by(source_task_id=task.id)
            .filter(AgentJob.worktree_path.isnot(None))
            .filter(AgentJob.status != "cancelled")
            .order_by(AgentJob.created_at.desc())
            .first()
        )
        if job and job.worktree_path and os.path.isdir(job.worktree_path):
            return job.worktree_path
    except Exception as e:
        logger.debug(f"[diff] AgentJob worktree fallback failed: {e}")
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
    """Return the unified diff of the agent's worktree vs the default branch.

    Tries a chain of diff strategies and returns the first non-empty
    result. Diagnostics ride along on every response so the frontend
    can show "no diff because <reason>" instead of just an empty page.

    Strategy order:
      1. git diff <merge-base(HEAD, origin/<default>)>
         — most correct: shows only what the agent actually did,
         agnostic to upstream commits.
      2. git diff origin/<default>
         — fallback when merge-base resolution failed (no origin ref,
         no shared ancestor). Less correct (upstream commits look like
         reverse-diffs) but better than nothing.
      3. git diff <merge-base(HEAD, <default>)> using the local branch
         — last-ditch when origin/<default> doesn't exist at all.

    Always combines committed + uncommitted edits via `git diff <ref>`
    (no `..HEAD`). Untracked files surface separately so the user can
    nudge the agent to `git add` them.
    """
    task, err = _task_or_404(task_id)
    if err:
        return err
    worktree = _worktree_path(task)
    if not worktree:
        return jsonify({
            "error": "No worktree for this task (yet)",
            "diag": {"task_extra_working_path": (task.extra or {}).get("working_path")},
        }), 400

    base_branch = _default_branch(worktree)

    # Resolve the parent commit each strategy diffs against.
    rc_mb_o, mb_origin, mb_origin_err = _git(
        ["merge-base", "HEAD", f"origin/{base_branch}"], cwd=worktree
    )
    rc_mb_l, mb_local, mb_local_err = _git(
        ["merge-base", "HEAD", base_branch], cwd=worktree
    )

    rc_h, head_sha, _ = _git(["rev-parse", "HEAD"], cwd=worktree)
    head_sha = head_sha.strip() if rc_h == 0 else ""

    # Try strategies in order. Each candidate is (label, ref).
    candidates = []
    if rc_mb_o == 0 and mb_origin.strip():
        candidates.append(("merge-base origin", mb_origin.strip()))
    candidates.append(("origin branch tip", f"origin/{base_branch}"))
    if rc_mb_l == 0 and mb_local.strip():
        candidates.append(("merge-base local", mb_local.strip()))
    candidates.append(("local branch tip", base_branch))

    raw = ""
    used_strategy = None
    base_sha = None
    last_err = ""
    attempts = []
    for label, ref in candidates:
        rc, out, err_out = _git(
            ["diff", "--no-color", ref], cwd=worktree, timeout=60,
        )
        attempts.append({
            "strategy": label, "ref": ref, "rc": rc,
            "diff_chars": len(out) if out else 0,
            "err": (err_out or "").strip()[:160] if rc != 0 else "",
        })
        if rc == 0 and out.strip():
            raw = out
            used_strategy = label
            base_sha = ref
            break
        if rc != 0:
            last_err = (err_out or "").strip()[:200]

    # Status for diagnostic context — even when the diff has content,
    # this tells the frontend whether the agent has uncommitted changes
    # still pending.
    rc_s, status_raw, _ = _git(
        ["status", "--porcelain"], cwd=worktree, timeout=10,
    )
    status_lines = []
    if rc_s == 0 and status_raw.strip():
        status_lines = [line for line in status_raw.splitlines() if line.strip()][:50]

    untracked = []
    rc_u, raw_u, _ = _git(
        ["ls-files", "--others", "--exclude-standard"],
        cwd=worktree, timeout=10,
    )
    if rc_u == 0 and raw_u.strip():
        untracked = [line for line in raw_u.splitlines() if line.strip()][:50]

    return jsonify({
        "task_id": task_id,
        "base_branch": base_branch,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "raw_diff": raw,
        "used_strategy": used_strategy,
        "untracked_files": untracked,
        "git_status": status_lines,
        # Diagnostic block — surfaced on every response so the
        # frontend can render "no diff because <reason>" rather than
        # leaving the user staring at an empty page wondering whether
        # the agent did nothing or the lookup misfired.
        "diag": {
            "worktree_path": worktree,
            "merge_base_origin_rc": rc_mb_o,
            "merge_base_origin_err": (mb_origin_err or "").strip()[:200] if rc_mb_o != 0 else "",
            "merge_base_local_rc": rc_mb_l,
            "merge_base_local_err": (mb_local_err or "").strip()[:200] if rc_mb_l != 0 else "",
            "attempts": attempts,
            "last_diff_err": last_err,
        },
    })


# ---------------------------------------------------------------------------
# Comment CRUD
# ---------------------------------------------------------------------------

@diff_bp.route("/tasks/<task_id>/comments", methods=["GET"])
def list_comments(task_id):
    """Return all comments for a task ordered by file, then line, then time."""
    real_task_id = _resolve_task_id(task_id)
    if real_task_id is None:
        return jsonify([])
    comments = (
        DiffComment.query
        .filter_by(task_id=real_task_id)
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

    real_task_id = _resolve_task_id(task_id)
    if real_task_id is None:
        return jsonify({"error": "task not found"}), 404

    # Validate parent_id belongs to the same task if provided
    parent_id = data.get("parent_id")
    if parent_id:
        parent = db.session.get(DiffComment, parent_id)
        if not parent or parent.task_id != real_task_id:
            return jsonify({"error": "parent_id does not belong to this task"}), 400

    comment = DiffComment(
        task_id=real_task_id,
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

    The MCP tool calls this with MAIKO_TASK_ID — which post-unification
    is the AgentJob.id. Resolve to the linked Task.id before insert so
    DiffComment.task_id's FK constraint to tasks.id is satisfied.
    """
    data = request.get_json() or {}
    if not data.get("body"):
        return jsonify({"error": "body is required"}), 400
    if "file_path" not in data or "line_number" not in data:
        return jsonify({"error": "file_path and line_number are required"}), 400
    real_task_id = _resolve_task_id(task_id)
    comment = DiffComment(
        task_id=real_task_id,
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
    """Wake the agent so it can address the reviewer's comments.

    Thin wrapper over wake_agent() — kept as a named helper so the
    three historical callers (request_changes, PR-comment processor,
    nudge endpoint) don't need to be rewritten everywhere. Returns
    True when the wake was either spawned or queued behind a current
    run; False on hard failure (no session, no worktree, no CLI).
    """
    from planet_maiko.agents.wake import wake_agent
    prompt = (
        "You have new review feedback. Call check_inbox to read it, "
        "address each comment, commit, and reply with "
        "message_type=\"ready_for_review\" when done."
    )
    ok, _mode = wake_agent(task_id, prompt, source="feedback", working_path=working_path)
    return ok


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

    # Same stale-pupdate cleanup as the approve path: the agent's old
    # ready_for_review was already acted on, so PackStatusPane shouldn't
    # keep advertising "ready for review" while the agent iterates.
    from planet_maiko.models.pupdate import Pupdate
    stale_reviews = (
        Pupdate.query
        .filter(Pupdate.type == "agent_ready_for_review")
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .filter(Pupdate.tags.contains(task_id))
        .all()
    )
    for p in stale_reviews:
        p.dismissed = True
    db.session.commit()

    # Post a single review message into the agent's inbox so it shows
    # up as one cohesive round on check_inbox, not N scattered ones.
    # Keyed by the canonical inbox id (the linked AgentJob.id) so the
    # agent's check_inbox actually finds it — writing under task_id
    # silently dropped the message into a parallel inbox the agent
    # never reads.
    from planet_maiko.models.agent_message import AgentMessage
    inbox_id = _canonical_inbox_id(task_id)
    review_body = _format_review_message(drafts)
    inbox_msg = AgentMessage(
        task_id=inbox_id,
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

    When the code window can't be extracted (file renamed, deleted,
    line shifted by edits before the user clicked Request Changes),
    we still emit a Signal so the rule pipeline sees the violation —
    we just skip the training pair since corrections.jsonl needs real
    code to be useful to the LoRA fine-tune.
    """
    from planet_maiko.brain.learning.feedback import (
        add_corrective_violation, _emit_signal,
    )
    for c in comments:
        code_snippet = _extract_code_window(worktree, c.file_path, c.line_number)
        if not code_snippet:
            logger.warning(
                f"[harvest] No code window for comment {c.id} "
                f"({c.file_path}:{c.line_number}); emitting signal "
                "without a training pair"
            )
            try:
                _emit_signal(
                    category="pattern",
                    text=c.body,
                    source_type="review_comment",
                    severity="suggestion",
                    repo=repo,
                    file_path=c.file_path,
                    code_context=None,
                )
            except Exception as e:
                logger.debug(f"[harvest] Signal emit failed for {c.id}: {e}")
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
        "plan_at": iso_utc(latest.created_at) if latest else None,
    })


def _resume_for_plan(task_id, working_path, instruction, plan_mode):
    """Resume the agent so it can act on a plan approval or revision.

    Routes through the wake orchestrator so it acquires the same
    per-task lock every other resume path uses. plan_mode=True
    re-applies --permission-mode plan via extra_args so a requested
    revision stays read-only until another approval.
    """
    from planet_maiko.agents.wake import wake_agent
    extra_args = ["--permission-mode", "plan"] if plan_mode else None
    ok, _mode = wake_agent(
        task_id, instruction, source="plan",
        working_path=working_path, extra_args=extra_args,
    )
    return ok


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
    # complete, then resume without plan mode. Keyed by canonical
    # inbox id so the agent's check_inbox sees it.
    db.session.add(AgentMessage(
        task_id=_canonical_inbox_id(task_id),
        direction="to_agent",
        sender="user",
        content="Plan approved. Go implement it — make the changes, commit locally, and call reply(message_type='ready_for_review') when done.",
        message_type="plan_approved",
    ))
    extra = dict(task.extra or {})
    extra["plan_approved_at"] = datetime.now(timezone.utc).isoformat()
    task.extra = extra

    # Same pattern as review/approve: dismiss the plan-approval
    # pupdate so PackStatusPane stops rendering "plan ready for
    # approval" after the user has already approved.
    from planet_maiko.models.pupdate import Pupdate
    stale_plans = (
        Pupdate.query
        .filter(Pupdate.type == "agent_plan_for_approval")
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .filter(Pupdate.tags.contains(task_id))
        .all()
    )
    for p in stale_plans:
        p.dismissed = True
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
        task_id=_canonical_inbox_id(task_id),
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
    """Hand the task back to the agent with an 'approved' message so
    the agent can push + open a PR following the repo's own conventions.

    Rationale for not running `gh pr create` from Maiko: PR creation
    is loaded with repo-specific conventions (templates, required
    labels, reviewers, draft vs. ready, release branch rules) that
    Maiko can't reliably reproduce. The agent is already logged in
    to `gh`, has access to `.github/PULL_REQUEST_TEMPLATE.md`, and
    knows the team's patterns via its LoRA / instructions. Better to
    say "approved, open the PR" and let it handle the nuance.

    Flow:
      - First approve (no pr_url yet): agent pushes + gh pr create,
        then reply(message_type="pr_opened", content=<url>)
        which the outbox handler uses to set task.extra.pr_url.
      - Subsequent approve (pr_url set): agent just pushes the
        updates; GitHub reflects new commits on the open PR
        automatically.

    Tasks stay open (status=in_review) until the PR merges
    (github_poller → _complete_review_task).
    """
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

    existing_pr_url = (task.extra or {}).get("pr_url")

    # Build the instruction we hand to the agent. Different wording
    # for first approve (open PR) vs subsequent (just push updates).
    if existing_pr_url:
        instruction = (
            f"Your updated changes are approved. Push branch "
            f"`{branch}` to origin so the existing PR ({existing_pr_url}) "
            f"picks up the new commits. You do NOT need to run "
            f"`gh pr create` — the PR is already open. If the push "
            f"fails (protected branch, diverged remote), reply with "
            f"message_type='stuck' and describe the error."
        )
    else:
        instruction = (
            f"Your work is approved. Time to open the PR:\n\n"
            f"1. Push branch `{branch}` to origin.\n"
            f"2. Run `gh pr create` following this repo's conventions —"
            f" respect any PR template at .github/PULL_REQUEST_TEMPLATE.md,"
            f" use appropriate labels, assign reviewers per team norms.\n"
            f"3. Once the PR is open, call "
            f"reply(message_type='pr_opened', content=<PR URL>) with "
            f"the URL on its own line.\n\n"
            f"Task: {task.title}\n\n"
            f"If you hit a problem (push rejected, gh auth missing, "
            f"template question), reply with message_type='stuck'."
        )

    from planet_maiko.agents.signature import signature_instruction_for_agent
    instruction += signature_instruction_for_agent(task.assigned_agent_id)

    from planet_maiko.models.agent_message import AgentMessage
    db.session.add(AgentMessage(
        task_id=_canonical_inbox_id(task_id),
        direction="to_agent",
        sender="user",
        content=instruction,
        message_type="approved",
    ))

    task.status = "in_review"
    task.updated_at = datetime.now(timezone.utc)
    extra = dict(task.extra or {})
    extra["last_approved_at"] = datetime.now(timezone.utc).isoformat()
    task.extra = extra

    # Dismiss the agent_ready_for_review pupdate(s) for this task so
    # PackStatusPane stops showing "ready for review" after the user
    # has already done the review. Without this the chip persists until
    # the next agent pupdate (pr_opened) lands — which can be minutes
    # away if the push is slow, or never if it fails.
    from planet_maiko.models.pupdate import Pupdate
    stale_reviews = (
        Pupdate.query
        .filter(Pupdate.type == "agent_ready_for_review")
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .filter(Pupdate.tags.contains(task_id))
        .all()
    )
    for p in stale_reviews:
        p.dismissed = True
    db.session.commit()

    # Resume the agent so it sees the approved message immediately.
    # Wake against the canonical inbox id so the resume targets the
    # session the agent is actually running on.
    resumed = _resume_agent_with_review(_canonical_inbox_id(task_id), worktree)

    return jsonify({
        "task_id": task_id,
        "branch": branch,
        "existing_pr_url": existing_pr_url,
        "agent_resumed": resumed,
    })
