"""Pupdate-scoped action handlers — require a triggering pupdate to
operate. Each manipulates that pupdate (dismiss, snapshot into a
spawned job, close linked tasks).
"""

import logging
import uuid
from datetime import datetime, timezone

from planet_maiko.database import db

from ._helpers import _pupdate_snapshot

logger = logging.getLogger(__name__)


def _act_spawn_agent_job_from_pupdate(automation, config, pupdate=None, context=None):
    """Pupdate-scope sibling of run_agent_job — spawns an AgentJob
    using the matched pupdate for context (repo, title).

    Config:
      kind, ask_first, description override, priority.
    """
    if pupdate is None:
        return {"skipped": "spawn_agent_job_from_pupdate requires pupdate context"}
    from planet_maiko.models.agent_job import AgentJob

    ask_first = bool(config.get("ask_first", False))
    kind = config.get("kind") or "investigation"
    repo = (pupdate.extra or {}).get("repo") or automation.scope_repo or None
    title = config.get("title") or f"{kind} triggered by {pupdate.type}"
    description = config.get("description") or pupdate.body or pupdate.title or ""
    priority = config.get("priority") or pupdate.priority or "normal"
    specialty_id = (config.get("specialty_id") or "").strip() or None

    extra = {
        "from_automation": automation.id,
        "triggered_by_pupdate": pupdate.id,
    }
    snap = _pupdate_snapshot(pupdate)
    if snap:
        extra["pupdate_snapshot"] = snap
    chain_ids = (context or {}).get("pupdate_ids") or []
    if chain_ids and chain_ids != [pupdate.id]:
        extra["triggered_by_pupdates"] = list(chain_ids)
    if specialty_id:
        extra["specialty_id"] = specialty_id

    # Ask-first → Memo, not a pending_approval AgentJob. Same rationale
    # as _act_run_agent_job: we don't mint jobs until the user has
    # decided they'll actually run.
    if ask_first:
        from planet_maiko.brain.memos import create_memo
        memo_extra = {
            **extra,
            "job_spec": {
                "kind": kind,
                "title": title,
                "description": description,
                "scope_repo": repo,
                "priority": priority,
                "automation_id": automation.id,
            },
        }
        memo = create_memo(
            kind="job_approval",
            category="offer",
            title=title,
            body=description or None,
            url=pupdate.url,
            priority=priority,
            cta_label="Approve",
            cta_action="approve",
            source_pupdate_id=pupdate.id,
            extra=memo_extra,
        )
        db.session.flush()
        return {
            "memo_id": memo.id,
            "kind": "spawn_agent_job_from_pupdate",
            "status": "awaiting_approval",
        }

    job_id = f"job-{uuid.uuid4().hex[:10]}"
    job = AgentJob(
        id=job_id,
        kind=kind,
        title=title,
        description=description,
        scope_repo=repo,
        priority=priority,
        created_by="automation",
        automation_id=automation.id,
        requires_approval=False,
        status="queued",
        approved_by="auto",
        approved_at=datetime.now(timezone.utc),
        extra=extra,
    )
    db.session.add(job)
    # Flush early so any DB error surfaces here (with a stack we can
    # see) rather than silently rolling back at the cycle's final
    # commit. Without this, a SQLite lock or constraint violation
    # downstream eats the AgentJob, the pupdate still gets marked
    # processed, and we end up with a "pupdate processed but no job"
    # mystery in the DB.
    try:
        db.session.flush()
    except Exception as e:
        logger.warning(
            f"[automation {automation.id}] AgentJob flush failed for "
            f"pupdate {pupdate.id}: {e}"
        )
        raise
    if not repo:
        logger.warning(
            f"[automation {automation.id}] spawn_agent_job_from_pupdate "
            f"created AgentJob {job_id} (kind={kind!r}) with NO scope_repo "
            f"— pupdate {pupdate.id} extra keys: "
            f"{sorted((pupdate.extra or {}).keys()) or '(empty)'}"
        )
    else:
        logger.info(
            f"[automation {automation.id}] spawned AgentJob {job_id} "
            f"(kind={kind!r}, scope_repo={repo!r}) from pupdate {pupdate.id}"
        )
    return {"job_id": job_id, "kind": "spawn_agent_job_from_pupdate", "status": job.status}


def _act_dismiss_pupdate(automation, config, pupdate=None, context=None):
    if pupdate is None:
        return {"skipped": "dismiss_pupdate requires pupdate context"}
    pupdate.dismissed = True
    pupdate.dismissed_at = datetime.now(timezone.utc)
    return {"kind": "dismiss_pupdate", "pupdate_id": pupdate.id}


def _act_create_task_from_pupdate(automation, config, pupdate=None, context=None):
    """Rule-style create-a-task: use the pupdate's title/priority as the
    task seed, letting config override task_type and task_priority.
    Mirrors _execute_create_task in the old processor.

    Dedupes on (url, type) — GitHub's review-request source_id includes
    the head SHA so every push to an open PR creates a fresh pupdate,
    which used to spawn a new task each time. If an open task of the
    same type already points at this PR, we skip and just link the new
    pupdate to the existing task via source_pupdate_id so the thread
    of activity stays together.
    """
    if pupdate is None:
        return {"skipped": "create_task_from_pupdate requires pupdate context"}
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route, is_ready

    task_type = config.get("task_type") or pupdate.type
    task_priority = config.get("task_priority") or pupdate.priority or "normal"

    if pupdate.url:
        existing = (
            Task.query
            .filter(Task.url == pupdate.url)
            .filter(Task.type == task_type)
            .filter(Task.status.notin_(["done", "cancelled"]))
            .first()
        )
        if existing:
            existing.source_pupdate_id = pupdate.id
            existing.updated_at = datetime.now(timezone.utc)

            # Refresh task.extra from the new pupdate's metadata. If
            # the original pupdate had stale or missing fields (older
            # poller version, GitHub API hiccup), the new pupdate is
            # the more authoritative source and we want downstream
            # consumers (scope_for_task, build_task_prompt) to read
            # the current values. Only fill keys that are missing or
            # empty on the existing task — don't clobber user edits.
            new_pup_extra = pupdate.extra or {}
            existing_extra = dict(existing.extra or {})
            for key in ("repo", "linear_id", "identifier",
                        "linear_cycle_id", "linear_cycle_number",
                        "linear_cycle_name"):
                if new_pup_extra.get(key) and not existing_extra.get(key):
                    existing_extra[key] = new_pup_extra[key]
                    logger.info(
                        f"[automation {automation.id}] task {existing.id}: "
                        f"backfilled extra.{key}={new_pup_extra[key]!r} "
                        f"from pupdate {pupdate.id}"
                    )
            existing.extra = existing_extra

            # If the task was parked in "waiting" (user posted their
            # review, ball in author's court), a fresh re-request
            # means the author wants another look — flip back to
            # "new" so it reappears in What-I'd-start-with and the
            # cycle's spawn_jobs_for_tasks phase picks it up for a
            # new review pass. Tasks in "new"/"in_progress" stay as
            # they are.
            status_flipped = False
            if existing.status == "waiting":
                existing.status = "new"
                status_flipped = True
                # Clear the old worktree pointer so the cycle's prep
                # phase re-preps against the PR's current HEAD. The
                # previous worktree's on an old SHA; the fresh review
                # pass needs the new commits the author just pushed.
                # cleanup_task_worktree tears down the old dir; the
                # prep phase rebuilds.
                extra = dict(existing.extra or {})
                wp = extra.get("working_path")
                branch = extra.get("branch")
                if wp and branch and ".maiko-worktrees" in wp:
                    try:
                        from planet_maiko.agents.runtime import cleanup
                        cleanup(wp, branch)
                    except Exception as e:
                        logger.debug(
                            f"[automation {automation.id}] "
                            f"stale worktree cleanup skipped: {e}"
                        )
                extra.pop("working_path", None)
                extra.pop("branch", None)
                extra.pop("session_id", None)
                existing.extra = extra
            return {
                "kind": "create_task_from_pupdate",
                "task_id": existing.id,
                "pupdate_id": pupdate.id,
                "deduped": True,
                "status_flipped": status_flipped,
            }

    task_id = f"task-{uuid.uuid4().hex[:10]}"
    pup_extra = pupdate.extra or {}
    repo = pup_extra.get("repo") or ""
    if not repo and task_type in ("review", "pr_review"):
        # Review tasks without a repo can't resolve a worktree later —
        # surface this loudly at creation time rather than discovering
        # it three cycles later when prepare() fails.
        logger.warning(
            f"[automation {automation.id}] "
            f"creating {task_type} task from pupdate {pupdate.id} "
            f"with NO repo — pupdate.extra keys: "
            f"{sorted((pup_extra or {}).keys()) or '(empty)'}"
        )
    extra = {
        "description": pupdate.body or "",
        "repo": repo,
        "from_automation": automation.id,
    }
    snap = _pupdate_snapshot(pupdate)
    if snap:
        extra["pupdate_snapshot"] = snap
    # Carry integration-specific identifiers through so downstream
    # sync (e.g. Linear status mirroring) can find the task by its
    # source id. Narrow list — don't leak unrelated pupdate fields.
    for key in (
        "linear_id", "identifier",
        "linear_cycle_id", "linear_cycle_number", "linear_cycle_name",
    ):
        if pup_extra.get(key) is not None:
            extra[key] = pup_extra[key]
    task = Task(
        id=task_id,
        title=pupdate.title,
        type=task_type,
        priority=task_priority,
        status="new",
        source_pupdate_id=pupdate.id,
        url=pupdate.url,
        tags=list(pupdate.tags or []),
        extra=extra,
    )
    db.session.add(task)
    db.session.flush()
    try:
        route(task)
    except Exception as e:
        # route() lazy-spawns an agent; if that fails the task is
        # left without an assigned agent and the spawn-jobs phase
        # will never pick it up — silently. Surface the failure so
        # we can see why the chain stalled.
        logger.warning(
            f"[automation {automation.id}] route(task={task.id}) failed: {e}"
        )
    if not task.assigned_agent_id and task_type in ("review", "pr_review"):
        logger.warning(
            f"[automation {automation.id}] task {task.id} ({task_type}) "
            f"created without an assigned agent — spawn_jobs_for_tasks "
            f"will skip it. Check route()/maybe_spawn for repo={repo!r}."
        )
    if not is_ready(task):
        task.status = "blocked"
    return {"kind": "create_task_from_pupdate", "task_id": task_id, "pupdate_id": pupdate.id}


def _act_complete_linked_task(automation, config, pupdate=None, context=None):
    """Close review / coding tasks whose url matches this pupdate's url.
    Replaces the old ACTION_COMPLETE_TASK in rules.py — same cleanup
    semantics, now living inside the Automation engine.

    Also cancels any queued/running AgentJob linked to those tasks
    (the unified kickoff path means the worktree + session live on
    AgentJob, not the Task), and dismisses every un-dismissed pupdate
    pointing at the same URL so the overview and ReviewQueue stop
    surfacing "reviewer requested" / "changes requested" cards for a
    PR that's already closed.
    """
    if pupdate is None or not pupdate.url:
        return {"skipped": "no url"}
    from planet_maiko.models.task import Task
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_job import AgentJob

    closed_review = 0
    closed_coding = 0
    cancelled_jobs = 0
    dismissed_linked = 0

    def _cleanup_task(t):
        """Close the task, tear down any task-side worktree, and cancel
        the linked AgentJob (which owns the real worktree + session
        post-unification). Returns nothing — caller increments counters."""
        nonlocal cancelled_jobs
        t.status = "done"
        t.updated_at = datetime.now(timezone.utc)

        # Legacy Task-side worktree (pre-unification kickoffs left the
        # path on task.extra). Still cleaned up here so older rows
        # don't leak directories.
        branch = (t.extra or {}).get("branch")
        wp = (t.extra or {}).get("working_path")
        if branch and wp and ".maiko-worktrees" in wp:
            try:
                from planet_maiko.agents.runtime import cleanup
                cleanup(wp, branch)
            except Exception as e:
                logger.debug(f"[automation {automation.id}] task-side worktree cleanup failed: {e}")

        # Cancel any AgentJob still working this task. Stops the
        # subprocess, cleans the AgentJob-owned worktree, marks the
        # row cancelled. Without this, a coding agent kept running
        # after the PR was merged because the AgentJob row stayed
        # "running" — stop_agent_session + cleanup must fire here.
        linked_jobs = (
            AgentJob.query
            .filter_by(source_task_id=t.id)
            .filter(AgentJob.status.in_(["queued", "running", "pending_approval"]))
            .all()
        )
        for job in linked_jobs:
            try:
                from planet_maiko.agents.runtime import (
                    stop_agent_session, cleanup as cleanup_worktree,
                )
                if job.status == "running":
                    try:
                        stop_agent_session(job.id)
                    except Exception as e:
                        logger.debug(
                            f"[automation {automation.id}] stop_agent_session "
                            f"({job.id}) failed: {e}"
                        )
                if job.worktree_path and job.branch and ".maiko-worktrees" in job.worktree_path:
                    try:
                        cleanup_worktree(job.worktree_path, job.branch)
                    except Exception as e:
                        logger.debug(
                            f"[automation {automation.id}] AgentJob worktree "
                            f"cleanup failed for {job.id}: {e}"
                        )
            except Exception as e:
                logger.debug(f"[automation {automation.id}] job cleanup imports failed: {e}")
            job.status = "cancelled"
            job.finished_at = datetime.now(timezone.utc)
            cancelled_jobs += 1

    review_tasks = Task.query.filter(
        Task.url == pupdate.url,
        Task.type.in_(["review", "pr_review"]),
        Task.status.in_(["new", "in_progress", "review"]),
    ).all()
    for t in review_tasks:
        _cleanup_task(t)
        closed_review += 1

    coding_tasks = Task.query.filter(
        Task.status.in_(["new", "in_progress", "in_review"]),
    ).all()
    for t in coding_tasks:
        if t.url == pupdate.url or (t.extra or {}).get("pr_url") == pupdate.url:
            _cleanup_task(t)
            closed_coding += 1

    # Second pass for AgentJobs whose linked Task was already closed
    # (or was never set) but whose row is still queued/running. The
    # task-driven loop above only fires when the Task is in an active
    # status; a Task that flipped to "done" early via a different
    # signal path leaves its job orphaned. Find all queued/running
    # jobs whose linked task points at this PR's URL OR whose extra
    # carries the pr URL directly, and cancel each.
    orphan_jobs = (
        AgentJob.query
        .filter(AgentJob.status.in_(["queued", "running", "pending_approval"]))
        .all()
    )
    for job in orphan_jobs:
        # source_task_id might point at a Task we've already closed in
        # this same pass — that's the case we're catching.
        linked_url = None
        if job.source_task_id:
            linked = db.session.get(Task, job.source_task_id)
            if linked is not None:
                linked_url = linked.url or (linked.extra or {}).get("pr_url")
        if linked_url is None:
            linked_url = (job.extra or {}).get("pr_url")
        if linked_url != pupdate.url:
            continue
        try:
            from planet_maiko.agents.runtime import (
                stop_agent_session, cleanup as cleanup_worktree,
            )
            if job.status == "running":
                try:
                    stop_agent_session(job.id)
                except Exception as e:
                    logger.debug(
                        f"[automation {automation.id}] stop_agent_session "
                        f"({job.id}) on orphan failed: {e}"
                    )
            if job.worktree_path and job.branch and ".maiko-worktrees" in job.worktree_path:
                try:
                    cleanup_worktree(job.worktree_path, job.branch)
                except Exception as e:
                    logger.debug(
                        f"[automation {automation.id}] orphan worktree "
                        f"cleanup failed for {job.id}: {e}"
                    )
        except Exception as e:
            logger.debug(f"[automation {automation.id}] orphan job cleanup imports failed: {e}")
        job.status = "cancelled"
        job.finished_at = datetime.now(timezone.utc)
        cancelled_jobs += 1

    # Dismiss all pupdates pointing at this URL (review_requested,
    # changes_requested, approved, merged, etc.) so the overview and
    # ReviewQueue stop showing cards for a PR that's closed. The
    # triggering pupdate itself is included — once we've acted on it,
    # it has no further value sitting in the inbox.
    linked_pupdates = (
        Pupdate.query
        .filter(Pupdate.url == pupdate.url)
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .all()
    )
    now = datetime.now(timezone.utc)
    for p in linked_pupdates:
        p.dismissed = True
        p.dismissed_at = now
        dismissed_linked += 1

    # Log every run so future "linked job didn't get cancelled" reports
    # are easy to diagnose — the line shows what we matched and what
    # we acted on against this specific pupdate's URL.
    logger.info(
        f"[automation {automation.id}] complete_linked_task on "
        f"{pupdate.url!r}: review_tasks={closed_review}, "
        f"coding_tasks={closed_coding}, agent_jobs_cancelled="
        f"{cancelled_jobs}, pupdates_dismissed={dismissed_linked}"
    )

    return {
        "kind": "complete_linked_task",
        "review_tasks_closed": closed_review,
        "coding_tasks_closed": closed_coding,
        "agent_jobs_cancelled": cancelled_jobs,
        "pupdates_dismissed": dismissed_linked,
    }
