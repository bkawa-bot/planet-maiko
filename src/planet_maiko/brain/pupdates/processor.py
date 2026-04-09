"""Pupdate processor - fetches unprocessed pupdates and runs them through the rules pipeline.

This is the brain's equivalent of fetch → decode → execute for pupdates:
    1. Fetch: query all pupdates where brain_processed=False
    2. Decode: evaluate each pupdate against the rules
    3. Execute: perform the matched action (dismiss, mark_read, create_task)
"""

import logging
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
    """Find and complete any open review task matching this PR URL."""
    review_tasks = Task.query.filter(
        Task.url == pr_url,
        Task.type.in_(["review", "pr_review"]),
        Task.status.in_(["new", "in_progress"]),
    ).all()

    for task in review_tasks:
        task.status = "done"
        task.updated_at = datetime.now(timezone.utc)
        logger.info(f"  -> auto-completed review task: {task.id}")


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
