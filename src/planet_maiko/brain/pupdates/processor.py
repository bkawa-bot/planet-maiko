"""Pupdate processor - fetches unprocessed pupdates and runs them through the rules pipeline.

This is the brain's equivalent of fetch → decode → execute for pupdates:
    1. Fetch: query all pupdates where brain_processed=False
    2. Decode: evaluate each pupdate against the rules
    3. Execute: perform the matched action (dismiss, mark_read, create_task)
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task
from planet_maiko.brain.pupdates.rules import evaluate, ACTION_DISMISS, ACTION_MARK_READ, ACTION_CREATE_TASK, ACTION_SKIP

logger = logging.getLogger(__name__)


def _try_llm_triage(pupdate):
    """Attempt LLM-based triage for an unmatched pupdate.

    Returns the triage result dict, or None if LLM is unavailable or disabled.
    """
    from planet_maiko.config import load_config
    brain_config = load_config().get("brain", {})
    if not brain_config.get("llm_triage", True):
        return None

    try:
        from planet_maiko.agents.brain_session import triage_pupdate
        result = triage_pupdate(pupdate)
        if result.get("action") != "skip":
            logger.info(f"  -> LLM triage: {result.get('action')} ({result.get('reason', '')})")
        return result
    except Exception as e:
        logger.debug(f"  -> LLM triage unavailable: {e}")
        return None


def _slugify(text, max_len=60):
    """Turn a title into a URL-safe task ID slug."""
    slug = text.lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = "-".join(slug.split())
    return slug[:max_len]


def _execute_dismiss(pupdate):
    """Dismiss a pupdate (archive it)."""
    pupdate.dismissed = True
    pupdate.dismissed_at = datetime.now(timezone.utc)
    pupdate.read = True
    logger.info(f"  -> dismissed: {pupdate.title}")


def _execute_mark_read(pupdate):
    """Mark a pupdate as read.

    For pr_approved/pr_merged, also complete the matching review task.
    """
    pupdate.read = True

    if pupdate.type in ("pr_approved", "pr_merged") and pupdate.url:
        _complete_review_task(pupdate.url)

    logger.info(f"  -> marked read: {pupdate.title}")


def _complete_review_task(pr_url):
    """Close any task whose linked PR just merged / got approved.

    Two match paths:
      1. Review-request tasks: task.type in (review, pr_review) AND
         task.url == pr_url — the user was asked to review someone
         else's PR and that PR's now merged, so the ask is done.
      2. Coding-agent tasks that opened a PR: any task where
         task.url == pr_url OR task.extra.pr_url == pr_url, in
         status new / in_progress / in_review. These are Maiko's own
         autonomous-coding outputs; they stay open through the review
         cycle and only close when the PR actually merges.
    """
    review_tasks = Task.query.filter(
        Task.url == pr_url,
        Task.type.in_(["review", "pr_review"]),
        Task.status.in_(["new", "in_progress"]),
    ).all()
    for task in review_tasks:
        task.status = "done"
        task.updated_at = datetime.now(timezone.utc)
        logger.info(f"  -> auto-completed review task: {task.id}")

    # Coding tasks whose PR matches — match either field so url set
    # via approve() or pre-existing task.url both work.
    coding_tasks = Task.query.filter(
        Task.status.in_(["new", "in_progress", "in_review"]),
    ).all()
    for task in coding_tasks:
        if task.url == pr_url or (task.extra or {}).get("pr_url") == pr_url:
            if task.status == "done":
                continue
            task.status = "done"
            task.updated_at = datetime.now(timezone.utc)
            # Now that the PR has landed, cleanup the worktree.
            branch = (task.extra or {}).get("branch")
            wp = (task.extra or {}).get("working_path")
            if branch and wp and ".maiko-worktrees" in wp:
                try:
                    from planet_maiko.agents.coding_agent import cleanup
                    repo_path = os.path.dirname(os.path.dirname(wp))
                    cleanup(repo_path, branch)
                except Exception as e:
                    logger.info(f"  -> worktree cleanup skipped for {task.id}: {e}")
            logger.info(f"  -> auto-completed coding task (PR merged): {task.id}")


def _resume_agent_for_pr_comments(pupdate):
    """Wake the coding agent so it can fetch + address new PR feedback.

    The pupdate carries task_id + pr_url. We post a message into the
    agent's inbox telling it to run `gh pr view N --comments` (since
    the agent already has the gh CLI available in the worktree),
    then fire `claude --resume <session>` so it actually runs. The
    agent iterates locally and ends with a fresh ready_for_review
    that the user reviews + approves in Maiko's diff viewer; approve
    pushes the new commits to the same PR branch.
    """
    extra = pupdate.extra or {}
    task_id = extra.get("task_id")
    pr_url = extra.get("pr_url")
    if not task_id:
        logger.warning(f"[pr-feedback] Pupdate {pupdate.id} has no task_id, skipping")
        return

    task = db.session.get(Task, task_id)
    if not task:
        logger.warning(f"[pr-feedback] Task {task_id} not found, skipping")
        return
    working_path = (task.extra or {}).get("working_path")
    if not working_path:
        logger.warning(f"[pr-feedback] Task {task_id} has no worktree, skipping")
        return

    # Drop a message into the agent's inbox so the conversation
    # history shows what triggered the resume, not just the prompt.
    from planet_maiko.models.agent_message import AgentMessage
    msg_body = (
        f"New review feedback was posted on the PR ({pr_url}).\n\n"
        f"Run `gh pr view {_extract_pr_number(pr_url)} --comments` "
        f"(or `gh api repos/<owner>/<repo>/pulls/<n>/comments` for "
        f"inline review comments) to read what the reviewers wrote, "
        f"then iterate on the changes. Commit locally — Maiko will "
        f"push the updates to the same branch after the user "
        f"approves the new diff."
    )
    db.session.add(AgentMessage(
        task_id=task_id,
        direction="to_agent",
        sender="maiko",
        content=msg_body,
        message_type="review",
    ))
    db.session.commit()

    # Fire the resume in a daemon thread — same pattern the local
    # request-changes flow uses.
    try:
        from planet_maiko.api.diff_api import _resume_agent_with_review
        _resume_agent_with_review(task_id, working_path)
    except Exception as e:
        logger.warning(f"[pr-feedback] Resume failed for {task_id}: {e}")


def _extract_pr_number(pr_url):
    """https://github.com/org/repo/pull/123 → '123'. Empty string on no match."""
    if not pr_url:
        return ""
    try:
        return pr_url.rstrip("/").split("/")[-1]
    except Exception:
        return ""


def _execute_create_task(pupdate, rule):
    """Create a task from a pupdate."""
    task_id = f"task-{_slugify(pupdate.title)}-{pupdate.id[:6]}"

    # Check if a task already exists for this pupdate
    existing = Task.query.filter_by(source_pupdate_id=pupdate.id).first()
    if existing:
        logger.info(f"  -> task already exists: {existing.id}")
        return

    task = Task(
        id=task_id,
        title=pupdate.title,
        type=rule.get("task_type", "todo"),
        status="new",
        priority=rule.get("task_priority", pupdate.priority),
        source_pupdate_id=pupdate.id,
        url=pupdate.url,
        tags=pupdate.tags or [],
        extra=pupdate.extra or {},
    )
    db.session.add(task)
    pupdate.read = True
    logger.info(f"  -> created task: {task_id}")


def process():
    """Run one processing cycle on all unprocessed pupdates.

    Returns:
        dict with counts: {processed, dismissed, read, tasks_created, skipped, unmatched}
    """
    unprocessed = Pupdate.query.filter_by(brain_processed=False, dismissed=False).all()

    if not unprocessed:
        return {"processed": 0, "dismissed": 0, "read": 0, "tasks_created": 0, "skipped": 0, "unmatched": 0}

    logger.info(f"Processing {len(unprocessed)} pupdate(s)...")

    counts = {"processed": 0, "dismissed": 0, "read": 0, "tasks_created": 0, "skipped": 0, "unmatched": 0, "held": 0}

    # Import focus manager for gating
    from planet_maiko.brain.focus.manager import should_surface, hold_pupdate

    for pupdate in unprocessed:
        # Focus mode gating: hold pupdates that shouldn't surface right now
        if not should_surface(pupdate):
            hold_pupdate(pupdate)
            counts["held"] += 1
            # Still process through rules (actions happen, just not surfaced)

        # PR-comment events on a Maiko-owned coding task → wake the
        # agent autonomously. The pupdate's source_id includes the
        # latest comment timestamp so the poller emits this exactly
        # once per new batch; we just translate the event to an
        # inbox message + claude --resume kick.
        if pupdate.type == "pr_review_commented":
            _resume_agent_for_pr_comments(pupdate)
            pupdate.brain_processed = True
            counts["processed"] += 1
            continue

        rule = evaluate(pupdate)

        if rule is None:
            # No rule matched - try LLM triage if available
            triage_result = _try_llm_triage(pupdate)
            if triage_result:
                action = triage_result.get("action", "skip")
                if action == ACTION_DISMISS:
                    _execute_dismiss(pupdate)
                    counts["dismissed"] += 1
                elif action == ACTION_MARK_READ:
                    _execute_mark_read(pupdate)
                    counts["read"] += 1
                elif action == ACTION_CREATE_TASK:
                    _execute_create_task(pupdate, {
                        "task_type": "todo",
                        "task_priority": triage_result.get("task_priority", pupdate.priority),
                    })
                    counts["tasks_created"] += 1
                else:
                    counts["unmatched"] += 1
            else:
                counts["unmatched"] += 1

            pupdate.brain_processed = True
            counts["processed"] += 1
            continue

        action = rule.get("action")

        if action == ACTION_DISMISS:
            _execute_dismiss(pupdate)
            counts["dismissed"] += 1
        elif action == ACTION_MARK_READ:
            _execute_mark_read(pupdate)
            counts["read"] += 1
        elif action == ACTION_CREATE_TASK:
            _execute_create_task(pupdate, rule)
            counts["tasks_created"] += 1
        elif action == ACTION_SKIP:
            counts["skipped"] += 1
        else:
            logger.warning(f"  Unknown action '{action}' in rule '{rule.get('name')}'")
            counts["unmatched"] += 1

        pupdate.brain_processed = True
        counts["processed"] += 1

    db.session.commit()

    # Auto-dismiss: informational pupdates older than 24h that are read
    stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
    stale = Pupdate.query.filter(
        Pupdate.read == True,
        Pupdate.dismissed == False,
        Pupdate.brain_processed == True,
        Pupdate.priority.in_(["low", "normal"]),
        Pupdate.timestamp < stale_threshold,
    ).limit(20).all()

    for p in stale:
        p.dismissed = True
        p.dismissed_at = datetime.now(timezone.utc)

    if stale:
        db.session.commit()
        logger.info(f"[processor] Auto-dismissed {len(stale)} stale read pupdates")

    logger.info(f"Cycle complete: {counts}")
    return counts
