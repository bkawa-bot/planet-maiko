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


# Maximum LLM triage calls per single processor pass. Above this, the
# cycle starts deferring unmatched pupdates for the next tick so a
# single burst of ambient pupdates (e.g. a backfill, or a noisy poller)
# can't fan out into dozens of LLM calls in one cycle.
# Configurable via brain.llm_triage_per_cycle; the default matches
# _phase_llm_triage's existing limit so both phases are consistent.
DEFAULT_MAX_LLM_TRIAGE_PER_CYCLE = 10


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


_PUPDATE_TYPE_TO_TASK_TYPE = {
    "pr_review_requested": "review",
    "pr_changes_requested": "bug",
    "pr_ci_failed": "bug",
    "linear_assigned": "coding",
    "linear_mention": "coding",
    "github_mention": "coding",
}


def _infer_task_type(pupdate, triage_result=None):
    """Pick a task type for an LLM-triaged create_task action.

    Order:
      1. Whatever the LLM explicitly returned (if it learned to set task_type).
      2. A static map keyed on pupdate.type for the well-known categories.
      3. "coding" as the catch-all default — better than "todo" because
         it tells the agent router this needs a coding agent.
    """
    if triage_result and triage_result.get("task_type"):
        return triage_result["task_type"]
    if pupdate.type in _PUPDATE_TYPE_TO_TASK_TYPE:
        return _PUPDATE_TYPE_TO_TASK_TYPE[pupdate.type]
    return "coding"


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
    # Grab just the IDs up front. triage_pupdate internally calls
    # db.session.close() to release the SQLite write lock before its
    # LLM call; that detaches every Pupdate object held in Python,
    # including the ones still in this loop. Iterating over IDs and
    # re-fetching each pass keeps every mutation on an attached row,
    # so dismiss / mark_read / brain_processed actually persist.
    # (Previously: every triage decision was logged but silently
    # dropped at commit time because the mutated object was detached.)
    pupdate_ids = [
        p.id for p in
        Pupdate.query
        .filter_by(brain_processed=False, dismissed=False)
        .with_entities(Pupdate.id)
        .all()
    ]

    if not pupdate_ids:
        return {"processed": 0, "dismissed": 0, "read": 0, "tasks_created": 0, "skipped": 0, "unmatched": 0}

    logger.info(f"Processing {len(pupdate_ids)} pupdate(s)...")

    counts = {
        "processed": 0, "dismissed": 0, "read": 0, "tasks_created": 0,
        "skipped": 0, "unmatched": 0, "held": 0,
        "llm_triage": 0, "llm_cached": 0, "llm_deferred": 0,
    }

    # Rate-limit LLM triage calls per processor pass. Backfill bursts
    # and noisy pollers used to fan out into dozens of LLM calls;
    # capping here keeps the token footprint predictable. Deferred
    # pupdates stay brain_processed=False and get picked up next cycle.
    from planet_maiko.config import load_config as _lc
    max_llm = int(
        _lc().get("brain", {}).get("llm_triage_per_cycle", DEFAULT_MAX_LLM_TRIAGE_PER_CYCLE)
    )

    # Within a single processor pass, cache LLM decisions by pupdate
    # type — if we asked "what do I do with pr_stale?" once, every
    # other pr_stale in this batch gets the same answer without another
    # LLM hit. This alone typically drops 70–90% of calls on bursty
    # days. `_execute_create_task` ignores triage_result.task_title
    # and uses pupdate.title directly, so caching is safe across
    # different pupdates of the same type.
    triage_cache_by_type = {}

    # Import focus manager for gating
    from planet_maiko.brain.focus.manager import should_surface, hold_pupdate

    for pid in pupdate_ids:
        pupdate = db.session.get(Pupdate, pid)
        if pupdate is None or pupdate.brain_processed or pupdate.dismissed:
            # Got consumed by another worker or a rule that already
            # committed — skip.
            continue

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
            db.session.commit()
            continue

        rule = evaluate(pupdate)

        if rule is None:
            # No rule matched → LLM triage path. Consult the type
            # cache first, then call out to the LLM, up to the per-
            # cycle cap.
            pupdate_type = pupdate.type
            triage_result = triage_cache_by_type.get(pupdate_type)
            used_cache = triage_result is not None

            if triage_result is None:
                if counts["llm_triage"] >= max_llm:
                    # Cap hit: defer this one to the next cycle. Leave
                    # brain_processed=False so the next pass picks it up.
                    counts["llm_deferred"] += 1
                    continue

                triage_result = _try_llm_triage(pupdate)
                pupdate = db.session.get(Pupdate, pid)
                if pupdate is None:
                    continue
                if triage_result is not None:
                    triage_cache_by_type[pupdate_type] = triage_result
                    counts["llm_triage"] += 1
            else:
                counts["llm_cached"] += 1

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
                        "task_type": _infer_task_type(pupdate, triage_result),
                        "task_priority": triage_result.get("task_priority", pupdate.priority),
                    })
                    counts["tasks_created"] += 1
                else:
                    counts["unmatched"] += 1
            else:
                counts["unmatched"] += 1

            pupdate.brain_processed = True
            counts["processed"] += 1
            # Commit per-pupdate so partial failures don't roll back
            # earlier triage decisions. Cache path commits too — same
            # semantics even without an LLM call.
            db.session.commit()
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
