"""Task orchestration phases.

  - projects: drive project phases forward
  - orchestrate: materialize investigation tasks + route unassigned tasks
  - unblock: cascade depends_on completion (blocked → new when deps land)
"""

import logging

logger = logging.getLogger(__name__)


_INVESTIGATION_TYPES = ("pr_ci_failed", "incident", "error_spike")


def _phase_projects():
    """Phase 6: Project driver — auto-advance project phases."""
    try:
        from planet_maiko.brain.projects.driver import drive_projects
        return drive_projects()
    except Exception as e:
        logger.warning(f"[cycle] Project driver error: {e}")
        return {"advanced": 0, "completed": 0, "error": str(e)}


def _phase_orchestrate():
    """Phase 8: Route unassigned tasks to agents and create tasks for
    investigable signals that haven't been materialized yet.

    Does three things per cycle:

    1. For each brain-processed investigable pupdate (CI failure, incident,
       error spike) without a spawned investigation task, create one.
    2. For every task with status in (new, blocked) and no
       assigned_agent_id, call route(task) to assign.
    3. Keep review tasks pointed at a review-role agent even if the
       original task_from_review_request rule assigned them generically.

    Idempotent — repeated runs are no-ops once everything is routed.
    """
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.task import Task
    from planet_maiko.database import db
    from planet_maiko.orchestration import route

    created = 0
    routed = 0

    try:
        # 1. Materialize investigation tasks for unprocessed signals.
        to_investigate = Pupdate.query.filter(
            Pupdate.type.in_(_INVESTIGATION_TYPES),
            Pupdate.brain_processed == True,  # noqa: E712
            Pupdate.dismissed == False,  # noqa: E712
            ~Pupdate.tags.contains("investigation_spawned"),
        ).limit(4).all()

        for p in to_investigate:
            meta = p.extra or {}
            repo = meta.get("repo") or meta.get("repository")
            task_id = f"investigation-{p.id[:24]}"
            if db.session.get(Task, task_id):
                p.tags = list(p.tags or []) + ["investigation_spawned"]
                continue
            t = Task(
                id=task_id,
                title=f"Investigate: {p.title}",
                type="investigation",
                status="new",
                priority=p.priority or "normal",
                source_pupdate_id=p.id,
                url=p.url,
                extra={"repo": repo} if repo else {},
                tags=["investigation"],
            )
            db.session.add(t)
            p.tags = list(p.tags or []) + ["investigation_spawned"]
            created += 1

        # 2. Route any still-unrouted active tasks.
        unrouted = Task.query.filter(
            Task.status.in_(["new", "blocked"]),
            Task.assigned_agent_id.is_(None),
        ).limit(50).all()
        for t in unrouted:
            try:
                agent_id = route(t)
                routed += 1
                logger.info(f"[cycle] Routed task {t.id} ({t.type}) -> {agent_id}")
            except Exception as e:
                # Warning, not debug: a silent routing failure leaves
                # the task with no agent and the user has no signal
                # anything's wrong.
                logger.warning(f"[cycle] route() failed for {t.id}: {e}")

        if created or routed:
            db.session.commit()
        return {"investigation_tasks_created": created, "routed": routed}
    except Exception as e:
        # Same reasoning — orchestrate failures left tasks unrouted
        # silently. Warn so it actually shows up in logs.
        logger.warning(f"[cycle] Orchestrate skipped: {e}")
        return {"investigation_tasks_created": 0, "routed": 0, "error": str(e)}


def _phase_unblock_tasks():
    """Phase 8b: Cascade dep completion — any blocked task whose deps are
    all done flips to "new" so its agent can pick it up."""
    from planet_maiko.models.task import Task
    from planet_maiko.database import db
    from planet_maiko.orchestration import is_ready

    try:
        blocked = Task.query.filter(Task.status == "blocked").all()
        unblocked = 0
        for t in blocked:
            if is_ready(t):
                t.status = "new"
                unblocked += 1
        if unblocked:
            db.session.commit()
            logger.info(f"[cycle] Unblocked {unblocked} task(s)")
        return {"unblocked": unblocked}
    except Exception as e:
        logger.warning(f"[cycle] Unblock phase skipped: {e}")
        return {"unblocked": 0}
