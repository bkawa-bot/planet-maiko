"""Diff review endpoints. Diff rendering, inline comments, and the
request-changes / approve actions that drive the autonomous coding
agent review loop.

Shape of the flow:
  1. Coding agent works headlessly in a worktree, commits, and sends a
     `ready_for_review` message. A pupdate lands in the inbox.
  2. The user opens the review page, fetches GET /jobs/<id>/diff and
     GET /jobs/<id>/comments, drafts comments, then either:
       - POST /jobs/<id>/review/request-changes posts comments to the
         agent's inbox and wakes the agent via a headless `claude --resume`
         thread; the agent iterates, commits, sends a fresh ready_for_review.
       - POST /jobs/<id>/review/approve hands the task back to the agent
         to push the branch and open a PR.

Agent-authored comments (from the leave_comment MCP tool) come in on
POST /jobs/<id>/comments/agent.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job_and_task_or_404(job_id):
    """Resolve a URL param (AgentJob.id) to (job, task, err).

    task is None when the job has no linked Task (cartograph runs,
    auto-spawned jobs from automations). err is a tuple suitable for
    Flask to return directly on 404.
    """
    from planet_maiko.models.agent_job import AgentJob
    job = db.session.get(AgentJob, job_id)
    if job is None:
        return None, None, (jsonify({"error": f"Job {job_id} not found"}), 404)
    task = db.session.get(Task, job.source_task_id) if job.source_task_id else None
    return job, task, None


def _worktree_path(job):
    """Return the agent's worktree path or None if it isn't on disk."""
    if job.worktree_path and os.path.isdir(job.worktree_path):
        return job.worktree_path
    return None


def _default_branch(worktree_path):
    """Resolve the default branch (main / master / trunk) of the parent
    repo so `git diff <base>..HEAD` compares against the right thing.
    Falls back to "main" when we can't tell. Picking wrong here just
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

@diff_bp.route("/jobs/<job_id>/diff", methods=["GET"])
def get_job_diff(job_id):
    """Return the unified diff of the agent's worktree vs the default branch.

    Tries a chain of diff strategies and returns the first non-empty
    result. Diagnostics ride along on every response so the frontend
    can show "no diff because <reason>" instead of just an empty page.

    Strategy order:
      1. git diff <merge-base(HEAD, origin/<default>)>
         most correct: shows only what the agent actually did,
         agnostic to upstream commits.
      2. git diff origin/<default>
         fallback when merge-base resolution failed (no origin ref,
         no shared ancestor). Less correct but better than nothing.
      3. git diff <merge-base(HEAD, <default>)> using the local branch.

    Always combines committed + uncommitted edits via `git diff <ref>`
    (no `..HEAD`). Untracked files surface separately so the user can
    nudge the agent to `git add` them.
    """
    job, task, err = _job_and_task_or_404(job_id)
    if err:
        return err
    worktree = _worktree_path(job)
    if not worktree:
        return jsonify({
            "error": "No worktree for this job (yet)",
            "diag": {"job_worktree_path": job.worktree_path},
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
        "job_id": job_id,
        "task_id": task.id if task else None,
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

@diff_bp.route("/jobs/<job_id>/comments", methods=["GET"])
def list_comments(job_id):
    """Return every diff comment for an agent run, ordered by file, line, time."""
    comments = (
        DiffComment.query
        .filter_by(job_id=job_id)
        .order_by(
            DiffComment.file_path.asc(),
            DiffComment.line_number.asc(),
            DiffComment.created_at.asc(),
        )
        .all()
    )
    return jsonify([c.to_dict() for c in comments])


@diff_bp.route("/jobs/<job_id>/comments", methods=["POST"])
def create_comment(job_id):
    """Create a comment. Default status=draft, author=user."""
    data = request.get_json() or {}
    if not data.get("body"):
        return jsonify({"error": "body is required"}), 400
    if "file_path" not in data or "line_number" not in data:
        return jsonify({"error": "file_path and line_number are required"}), 400

    parent_id = data.get("parent_id")
    if parent_id:
        parent = db.session.get(DiffComment, parent_id)
        if not parent or parent.job_id != job_id:
            return jsonify({"error": "parent_id does not belong to this job"}), 400

    comment = DiffComment(
        job_id=job_id,
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

@diff_bp.route("/jobs/<job_id>/comments/agent", methods=["POST"])
def create_agent_comment(job_id):
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
        job_id=job_id,
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


def _resume_agent_with_review(job_id, working_path):
    """Wake the agent so it can address the reviewer's comments.

    Returns True when the wake was either spawned or queued behind a
    current run, False on hard failure (no session, no worktree, no CLI).
    """
    from planet_maiko.agents.wake import wake_agent
    prompt = (
        "You have new review feedback. Call check_inbox to read it, "
        "address each comment, commit, and reply with "
        "message_type=\"ready_for_review\" when done."
    )
    ok, _mode = wake_agent(job_id, prompt, source="feedback", working_path=working_path)
    return ok


@diff_bp.route("/jobs/<job_id>/review/request-changes", methods=["POST"])
def request_changes(job_id):
    """Submit all draft comments and wake the agent up to iterate."""
    job, task, err = _job_and_task_or_404(job_id)
    if err:
        return err
    worktree = _worktree_path(job)
    if not worktree:
        return jsonify({"error": "No worktree for this job"}), 400

    drafts = (
        DiffComment.query
        .filter_by(job_id=job_id, status="draft", author="user")
        .order_by(DiffComment.file_path.asc(), DiffComment.line_number.asc())
        .all()
    )
    if not drafts:
        return jsonify({"error": "No draft comments to submit"}), 400

    for c in drafts:
        c.status = "submitted"

    # Dismiss any stale "ready for review" pupdates so PackStatusPane
    # stops advertising the old state while the agent iterates.
    if task is not None:
        from planet_maiko.models.pupdate import Pupdate
        stale_reviews = (
            Pupdate.query
            .filter(Pupdate.type == "agent_ready_for_review")
            .filter(Pupdate.dismissed == False)  # noqa: E712
            .filter(Pupdate.tags.contains(task.id))
            .all()
        )
        for p in stale_reviews:
            p.dismissed = True
    db.session.commit()

    from planet_maiko.models.agent_message import AgentMessage
    review_body = _format_review_message(drafts)
    db.session.add(AgentMessage(
        task_id=job_id,
        direction="to_agent",
        sender="user",
        content=review_body,
        message_type="review",
    ))
    db.session.commit()

    # Harvest each comment as a corrective-VIOLATION training pair.
    # The user's comment body is the violation the model should have
    # caught; pull a small code window so the pair has context.
    try:
        repo = job.scope_repo or (task.extra or {}).get("repo") if task else job.scope_repo
        _harvest_comments_as_training_pairs(drafts, worktree, repo)
    except Exception as e:
        logger.warning(f"[harvest] Local comment harvest failed for {job_id}: {e}")

    resumed = _resume_agent_with_review(job_id, worktree)

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


@diff_bp.route("/jobs/<job_id>/plan", methods=["GET"])
def get_plan(job_id):
    """Return the latest plan_for_approval content the agent sent, plus
    the task's plan_approved_at timestamp so the UI can tell the user
    what state they're in.
    """
    job, task, err = _job_and_task_or_404(job_id)
    if err:
        return err
    from planet_maiko.models.agent_message import AgentMessage
    latest = (
        AgentMessage.query
        .filter_by(task_id=job_id, direction="from_agent", message_type="plan_for_approval")
        .order_by(AgentMessage.created_at.desc())
        .first()
    )
    extra = (task.extra if task else {}) or {}
    return jsonify({
        "job_id": job_id,
        "task_id": task.id if task else None,
        "plan_first": bool(extra.get("plan_first")),
        "plan_approved_at": extra.get("plan_approved_at"),
        "plan": latest.content if latest else None,
        "plan_at": iso_utc(latest.created_at) if latest else None,
    })


def _resume_for_plan(job_id, working_path, instruction, plan_mode):
    """Resume the agent so it can act on a plan approval or revision.

    Routes through the wake orchestrator so it acquires the same
    per-job lock every other resume path uses. plan_mode=True
    re-applies --permission-mode plan via extra_args so a requested
    revision stays read-only until another approval.
    """
    from planet_maiko.agents.wake import wake_agent
    extra_args = ["--permission-mode", "plan"] if plan_mode else None
    ok, _mode = wake_agent(
        job_id, instruction, source="plan",
        working_path=working_path, extra_args=extra_args,
    )
    return ok


@diff_bp.route("/jobs/<job_id>/plan/approve", methods=["POST"])
def approve_plan(job_id):
    """User approved the agent's proposed plan: resume without plan
    mode so the agent can actually write code now.
    """
    from planet_maiko.models.agent_message import AgentMessage

    job, task, err = _job_and_task_or_404(job_id)
    if err:
        return err
    worktree = _worktree_path(job)
    if not worktree:
        return jsonify({"error": "No worktree for this job"}), 400

    db.session.add(AgentMessage(
        task_id=job_id,
        direction="to_agent",
        sender="user",
        content="Plan approved. Go implement it. Make the changes, commit locally, and call reply(message_type='ready_for_review') when done.",
        message_type="plan_approved",
    ))
    if task is not None:
        extra = dict(task.extra or {})
        extra["plan_approved_at"] = datetime.now(timezone.utc).isoformat()
        task.extra = extra

        # Dismiss the plan-approval pupdate so PackStatusPane stops
        # showing "plan ready for approval" after the user approved.
        from planet_maiko.models.pupdate import Pupdate
        stale_plans = (
            Pupdate.query
            .filter(Pupdate.type == "agent_plan_for_approval")
            .filter(Pupdate.dismissed == False)  # noqa: E712
            .filter(Pupdate.tags.contains(task.id))
            .all()
        )
        for p in stale_plans:
            p.dismissed = True
    db.session.commit()

    resumed = _resume_for_plan(
        job_id, worktree,
        instruction=(
            "Your plan was approved. Implement it now: follow the plan, "
            "commit your changes locally, and call "
            "reply(message_type='ready_for_review') when you're ready for "
            "the user to review the diff. Don't git push."
        ),
        plan_mode=False,
    )
    return jsonify({"job_id": job_id, "agent_resumed": resumed, "mode": "implementing"})


@diff_bp.route("/jobs/<job_id>/plan/revise", methods=["POST"])
def revise_plan(job_id):
    """User wants the agent to revise the plan before implementing."""
    from planet_maiko.models.agent_message import AgentMessage

    job, task, err = _job_and_task_or_404(job_id)
    if err:
        return err
    worktree = _worktree_path(job)
    if not worktree:
        return jsonify({"error": "No worktree for this job"}), 400

    data = request.get_json(silent=True) or {}
    feedback = (data.get("feedback") or "").strip()
    if not feedback:
        return jsonify({"error": "feedback is required"}), 400

    db.session.add(AgentMessage(
        task_id=job_id,
        direction="to_agent",
        sender="user",
        content=feedback,
        message_type="plan_revision",
    ))
    db.session.commit()

    resumed = _resume_for_plan(
        job_id, worktree,
        instruction=(
            "The user reviewed your plan and has feedback. Revise the "
            "plan based on their comments and call "
            "reply(message_type='plan_for_approval') with the updated "
            "version. Do NOT write code yet.\n\n"
            f"User feedback:\n{feedback}"
        ),
        plan_mode=True,
    )
    return jsonify({"job_id": job_id, "agent_resumed": resumed, "mode": "revising"})


@diff_bp.route("/jobs/<job_id>/review/approve", methods=["POST"])
def approve(job_id):
    """Hand the task back to the agent with an 'approved' message so
    the agent can push + open a PR following the repo's own conventions.

    PR creation is repo-specific (templates, labels, reviewers, draft
    vs. ready, release branch rules) so Maiko delegates instead of
    trying to reproduce conventions. The agent is already authed to
    `gh` and knows its team's patterns via its LoRA / instructions.

    Flow:
      - First approve (no pr_url): agent pushes + gh pr create, then
        replies pr_opened with the URL.
      - Subsequent approve (pr_url set): agent just pushes; GitHub
        reflects new commits on the open PR automatically.

    Tasks stay open (status=in_review) until the PR merges
    (github_poller → _complete_review_task).
    """
    job, task, err = _job_and_task_or_404(job_id)
    if err:
        return err
    worktree = _worktree_path(job)
    if not worktree:
        return jsonify({"error": "No worktree for this job"}), 400

    # Branch / PR url / title / agent come from the linked Task in the
    # task-centric review flow, else from the job itself — a task-less
    # workflow coder job (each fanned branch is independently PR-able)
    # carries them on the job.
    branch = (task.extra or {}).get("branch") if task else job.branch
    if not branch:
        return jsonify({"error": "No branch tracked for this job"}), 400
    title = task.title if task else (job.title or "this work")
    agent_id = task.assigned_agent_id if task else job.agent_profile_id

    submitted = DiffComment.query.filter_by(
        job_id=job_id, status="submitted",
    ).all()
    for c in submitted:
        c.status = "resolved"

    existing_pr_url = (
        (task.extra or {}).get("pr_url") if task else (job.extra or {}).get("pr_url")
    )
    # Stacked child: the scatter cut this worktree from a parent task's branch,
    # so its PR must target that branch (not the default), or the diff would
    # include the parent's commits too.
    parent_branch = (job.extra or {}).get("parent_branch")

    if existing_pr_url:
        instruction = (
            f"Your updated changes are approved. Push branch "
            f"`{branch}` to origin so the existing PR ({existing_pr_url}) "
            f"picks up the new commits. You do NOT need to run "
            f"`gh pr create`. The PR is already open. If the push "
            f"fails (protected branch, diverged remote), reply with "
            f"message_type='stuck' and describe the error."
        )
    else:
        base_note = (
            f" This work stacks on `{parent_branch}`, so open the PR against "
            f"that branch: pass `--base {parent_branch}` to `gh pr create` (not "
            f"the default branch), so the PR shows only your changes."
            if parent_branch else ""
        )
        instruction = (
            f"Your work is approved. Time to open the PR:\n\n"
            f"1. Push branch `{branch}` to origin.\n"
            f"2. Run `gh pr create` following this repo's conventions:"
            f" respect any PR template at .github/PULL_REQUEST_TEMPLATE.md,"
            f" use appropriate labels, assign reviewers per team norms.{base_note}\n"
            f"3. Once the PR is open, call "
            f"reply(message_type='pr_opened', content=<PR URL>) with "
            f"the URL on its own line.\n\n"
            f"Task: {title}\n\n"
            f"If you hit a problem (push rejected, gh auth missing, "
            f"template question), reply with message_type='stuck'."
        )

    from planet_maiko.agents.signature import signature_instruction_for_agent
    instruction += signature_instruction_for_agent(agent_id)

    from planet_maiko.models.agent_message import AgentMessage
    db.session.add(AgentMessage(
        task_id=job_id,
        direction="to_agent",
        sender="user",
        content=instruction,
        message_type="approved",
    ))

    if task is not None:
        task.status = "in_review"
        task.updated_at = datetime.now(timezone.utc)
        extra = dict(task.extra or {})
        extra["last_approved_at"] = datetime.now(timezone.utc).isoformat()
        task.extra = extra

        from planet_maiko.models.pupdate import Pupdate
        stale_reviews = (
            Pupdate.query
            .filter(Pupdate.type == "agent_ready_for_review")
            .filter(Pupdate.dismissed == False)  # noqa: E712
            .filter(Pupdate.tags.contains(task.id))
            .all()
        )
        for p in stale_reviews:
            p.dismissed = True
    db.session.commit()

    resumed = _resume_agent_with_review(job_id, worktree)

    return jsonify({
        "job_id": job_id,
        "task_id": task.id if task else None,
        "branch": branch,
        "existing_pr_url": existing_pr_url,
        "agent_resumed": resumed,
    })
