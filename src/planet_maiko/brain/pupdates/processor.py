"""Pupdate processor — handles the two responsibilities that didn't
fold into the Automation engine:

    1. Focus-mode gating: mark held pupdates during soft/deep focus
       so they don't surface until the user is back on available.
    2. pr_review_commented → agent wake: resume the Maiko-owned coding
       agent whose PR just got new comments. Complex state logic
       (session registry, inbox messaging) that doesn't fit the
       declarative when/then dispatch.

Everything else — dismiss, create_task, complete_task — moved to the
Automation engine as pupdate-scope automations. The engine runs
before this phase and marks matching pupdates brain_processed, so
by the time we get here we're only seeing what it didn't handle.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)


def _resume_agent_for_pr_comments(pupdate):
    """Wake the coding agent so it can fetch + address new PR feedback.

    Posts a message into the agent's inbox pointing at the PR and
    resumes the claude session so the agent can iterate.

    Also emits a visible signal — either an `agent_working_on_feedback`
    pupdate (on successful wake) or an `agent_stuck` pupdate (when
    the resume fails). Without this, the pr_review_commented event
    is silent: the user gets no feedback that comments landed OR
    that their agent couldn't be woken up.

    Training-signal harvesting on open PRs was removed: the merged-
    PR scrape in github_poller._after_sync() is authoritative for
    LoRA training data, so doing both produced duplicates. Anything
    a reviewer writes here gets captured on merge.
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
        _emit_agent_stuck_on_missing_worktree(task, pr_url, pupdate)
        return

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

    resumed = False
    try:
        from planet_maiko.api.diff_api import _resume_agent_with_review
        resumed = bool(_resume_agent_with_review(task_id, working_path))
    except Exception as e:
        logger.warning(f"[pr-feedback] Resume failed for {task_id}: {e}")

    if resumed:
        _emit_agent_working_on_feedback(task, pr_url, pupdate)
    else:
        _emit_agent_stuck_on_missing_session(task, pr_url, pupdate)


def _emit_agent_working_on_feedback(task, pr_url, source_pupdate):
    """Transient pupdate so the user sees "agent is iterating on the
    reviewer's comments" in the Pack Requests widget, instead of the
    flow being silent for 10-20 minutes until the next ready_for_review.
    Clears when the user dismisses or when the next ready_for_review
    lands (the widget just shows whatever's currently non-dismissed).
    """
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile
    import uuid as _uuid

    agent_name = "The agent"
    if task.assigned_agent_id:
        a = db.session.get(AgentProfile, task.assigned_agent_id)
        if a:
            agent_name = a.display_name

    pupdate = Pupdate(
        id=f"agent-working-{task.id}-{_uuid.uuid4().hex[:8]}",
        source="maiko",
        source_id=f"agent-working/{task.id}/{source_pupdate.id}",
        type="agent_working_on_feedback",
        priority="normal",
        title=f"{agent_name} is addressing review feedback",
        body=f"{agent_name} woke up on new comments from the PR ({pr_url}) and is iterating.",
        url=pr_url,
        actionable=False,
        tags=[task.id, "agent"],
        extra={
            "task_id": task.id,
            "agent_id": task.assigned_agent_id,
            "pr_url": pr_url,
        },
        brain_processed=True,
    )
    db.session.add(pupdate)
    db.session.commit()


def _emit_agent_stuck_on_missing_session(task, pr_url, source_pupdate):
    """The agent's claude session couldn't be resumed — most likely
    the session_id was never registered or the worktree's cleaned up.
    Fail loudly rather than silently mark brain_processed and move on,
    because the alternative is the reviewer's comments land and the
    agent never sees them, and the user has no idea why.
    """
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile
    import uuid as _uuid

    agent_name = "an agent"
    if task.assigned_agent_id:
        a = db.session.get(AgentProfile, task.assigned_agent_id)
        if a:
            agent_name = a.display_name

    pupdate = Pupdate(
        id=f"agent-stuck-wake-{task.id}-{_uuid.uuid4().hex[:8]}",
        source="maiko",
        source_id=f"agent-stuck-wake/{task.id}/{source_pupdate.id}",
        type="agent_stuck",
        priority="high",
        title=f"Couldn't wake {agent_name} on new PR comments",
        body=(
            f"Reviewer comments landed on the PR ({pr_url}) but I couldn't "
            f"resume the agent's session — probably the session id wasn't "
            f"registered or the worktree has been cleaned up. You'll want "
            f"to open the task and decide what to do next."
        ),
        url=pr_url,
        actionable=True,
        action_hint="Help the agent",
        tags=[task.id, "agent", "stuck", "wake-failed"],
        extra={
            "task_id": task.id,
            "agent_id": task.assigned_agent_id,
            "pr_url": pr_url,
            "reason": "session_resume_failed",
        },
        brain_processed=True,
    )
    db.session.add(pupdate)
    db.session.commit()


def _emit_agent_stuck_on_missing_worktree(task, pr_url, source_pupdate):
    """Same shape as the session-miss case — just a different reason
    string so the user can tell at a glance which part of the wake
    broke."""
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile
    import uuid as _uuid

    agent_name = "an agent"
    if task.assigned_agent_id:
        a = db.session.get(AgentProfile, task.assigned_agent_id)
        if a:
            agent_name = a.display_name

    pupdate = Pupdate(
        id=f"agent-stuck-wt-{task.id}-{_uuid.uuid4().hex[:8]}",
        source="maiko",
        source_id=f"agent-stuck-wt/{task.id}/{source_pupdate.id}",
        type="agent_stuck",
        priority="high",
        title=f"Couldn't wake {agent_name} on new PR comments",
        body=(
            f"Reviewer comments landed on the PR ({pr_url}) but the task "
            f"has no worktree on disk — it may have been cleaned up "
            f"already. Reassign the task if you want the agent to "
            f"respond to this round of feedback."
        ),
        url=pr_url,
        actionable=True,
        action_hint="Reassign or close the task",
        tags=[task.id, "agent", "stuck", "worktree-missing"],
        extra={
            "task_id": task.id,
            "agent_id": task.assigned_agent_id,
            "pr_url": pr_url,
            "reason": "worktree_missing",
        },
        brain_processed=True,
    )
    db.session.add(pupdate)
    db.session.commit()


def _extract_pr_number(pr_url):
    """https://github.com/org/repo/pull/123 → '123'. Empty string on no match."""
    if not pr_url:
        return ""
    try:
        return pr_url.rstrip("/").split("/")[-1]
    except Exception:
        return ""


def process():
    """Run one processing cycle on all unprocessed pupdates.

    Returns:
        dict with counts: {processed, dismissed, read, tasks_created, skipped, unmatched}
    """
    # Grab just the IDs up front and re-fetch per iteration. The
    # original motivation was the old LLM-triage path closing the DB
    # session mid-loop (detaching rows); that's gone now, but the
    # cheap re-fetch is harmless and defends against any future
    # action handler that needs to release the session.
    pupdate_ids = [
        p.id for p in
        Pupdate.query
        .filter_by(brain_processed=False, dismissed=False)
        .with_entities(Pupdate.id)
        .all()
    ]

    if not pupdate_ids:
        return {"processed": 0, "dismissed": 0, "tasks_created": 0, "tasks_completed": 0, "skipped": 0, "unmatched": 0}

    logger.info(f"Processing {len(pupdate_ids)} pupdate(s)...")

    counts = {"processed": 0, "held": 0, "pr_comments_woke": 0, "unmatched": 0}

    from planet_maiko.brain.focus.manager import should_surface, hold_pupdate

    for pid in pupdate_ids:
        pupdate = db.session.get(Pupdate, pid)
        if pupdate is None or pupdate.brain_processed or pupdate.dismissed:
            # Claimed by the Automation engine or another worker.
            continue

        # Focus mode gating: mark held without processing (lets a later
        # focus-exit release them in the digest).
        if not should_surface(pupdate):
            hold_pupdate(pupdate)
            counts["held"] += 1

        # PR-comment events wake the coding agent. State-heavy, so
        # handled here rather than as an automation action.
        if pupdate.type == "pr_review_commented":
            _resume_agent_for_pr_comments(pupdate)
            pupdate.brain_processed = True
            counts["pr_comments_woke"] += 1
            counts["processed"] += 1
            db.session.commit()
            continue

        # Anything else that reached this phase wasn't matched by an
        # Automation. Mark it processed so we don't keep reconsidering
        # it every cycle; it stays visible (not dismissed). The signal
        # is: add an Automation if this type should auto-handle.
        pupdate.brain_processed = True
        counts["unmatched"] += 1
        counts["processed"] += 1

    db.session.commit()

    # Auto-dismiss: low-priority pupdates older than 24h that have
    # already been processed. Previously this also required read=True,
    # but the read flag has been removed; any low-priority informational
    # pupdate that's been through the processor once is fair game.
    stale_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
    stale = Pupdate.query.filter(
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
        logger.info(f"[processor] Auto-dismissed {len(stale)} stale pupdates")

    logger.info(f"Cycle complete: {counts}")
    return counts
