"""Shared cross-phase helpers.

Lives here (rather than in a single phase file) so any phase can
import without setting up a circular dependency with cycle.py.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _bump_agent_failed(agent_profile_id):
    """Increment the agent profile's tasks_failed counter. No-op when
    the profile id is unknown (stray job with no assigned agent).
    Caller is responsible for committing the session.

    Both _phase_spawn_jobs_for_tasks and _phase_execute_agent_jobs
    need this — it lives here rather than in either of those so
    execute_jobs doesn't have to reach into spawn_jobs (which used
    to cause a NameError on every kickoff failure path because
    execute_jobs called the function without importing it; the
    cycle's outer except swallowed it and the job stayed queued).
    """
    if not agent_profile_id:
        return
    from planet_maiko.models.agent_profile import AgentProfile as _AP
    from planet_maiko.database import db as _db
    prof = _db.session.get(_AP, agent_profile_id)
    if prof is not None:
        prof.tasks_failed = (prof.tasks_failed or 0) + 1


def _emit_missing_clone_pupdate(task, repo):
    """Surface "I can't find a local clone for this repo" as an
    actionable pupdate, dedup'd by repo so the user gets one entry
    per missing repo, not one per stuck task per cycle.

    Without this, review tasks for repos missing from
    config.github.repo_roots silently sit unrouted forever — agent is
    assigned, no worktree ever appears, AgentsActiveTab stays empty,
    and the user has no signal anything is wrong.
    """
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.database import db

    if not repo:
        repo = "(unknown repo)"

    source_id = f"missing-clone/{repo}"
    existing = Pupdate.query.filter_by(source_id=source_id).first()
    if existing and not existing.dismissed:
        # Already surfaced this repo; don't pile up duplicates.
        return

    if existing and existing.dismissed:
        # User dismissed but the problem is back — resurrect.
        existing.dismissed = False
        existing.dismissed_at = None
        existing.timestamp = datetime.now(timezone.utc)
        existing.body = (
            f"Maiko routed a {task.type} task ({task.id}) to an agent but "
            f"can't find a local clone of {repo} on disk. Add the parent "
            f"directory to Settings → GitHub → Repo Roots so worktrees "
            f"can be created."
        )
        existing.tags = list(set((existing.tags or []) + [repo, task.id]))
        db.session.flush()
        return

    p = Pupdate(
        id=f"missing-clone-{repo.replace('/', '-')}"[:64],
        source="maiko",
        source_id=source_id,
        type="missing_local_clone",
        priority="high",
        title=f"Can't find a local clone for {repo}",
        body=(
            f"Maiko routed a {task.type} task ({task.id}) to an agent but "
            f"can't find a local clone of {repo} on disk. Add the parent "
            f"directory to Settings → GitHub → Repo Roots so worktrees "
            f"can be created."
        ),
        actionable=True,
        action_hint="Open Settings",
        tags=[repo, task.id, "missing_clone"],
        extra={"repo": repo, "task_id": task.id},
    )
    db.session.add(p)
    db.session.flush()


def _memo_job_failed(job):
    """Surface a failed AgentJob as a memo so the failure isn't silent.

    Failed jobs no longer auto-retry (see _phase_spawn_jobs_for_tasks),
    so the user needs a visible nudge to fix the cause and relaunch
    instead of the failure just sitting in Recent Failures. Caller is
    responsible for committing the session.
    """
    try:
        from planet_maiko.brain.memos import create_memo
        label = (job.title or job.kind or "agent job")
        body = (job.error or "No error detail.").strip()[:1000]
        if job.scope_repo:
            body += f"\n\nRepo: {job.scope_repo}"
        body += (
            "\n\nThis won't retry on its own. Fix the cause, then "
            "relaunch the task."
        )
        create_memo(
            kind="notification",
            category="waiting",
            title=f"Agent job failed: {label[:120]}",
            body=body,
            priority=(job.priority or "normal"),
            source_agent_id=job.agent_profile_id,
            source_task_id=job.source_task_id,
        )
    except Exception as e:
        logger.warning(
            f"[cycle] job-failed memo skipped for "
            f"{getattr(job, 'id', '?')}: {e}"
        )
