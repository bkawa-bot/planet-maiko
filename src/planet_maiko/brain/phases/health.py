"""Agent + task health phases.

  - stuck_check: flag agents whose claude process exited silently
  - stuck_escalation: surface tasks stuck in_progress for too long
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _phase_stuck_check():
    """Phase 5: Flag agents whose claude process exited silently.

    Surfaces an `agent_stuck` pupdate so the user sees it in Pack Requests
    and can decide whether to nudge or abandon. No auto-wake — the wake-
    on-message path handles the case where the user actually wants to
    re-engage.
    """
    try:
        from flask import current_app
        from planet_maiko.agents.wake import check_stuck_agents
        return {"flagged": check_stuck_agents(current_app._get_current_object())}
    except Exception as e:
        logger.warning(f"[cycle] Stuck-agent check error: {e}")
        return {"flagged": 0, "error": str(e)}


def _phase_stuck_escalation():
    """Phase 8d: Surface tasks stuck in_progress for too long as a
    high-priority "needs rescue" pupdate.

    A task in_progress whose updated_at is older than STUCK_DAYS gets a
    single escalation pupdate (dedup by source_id). The user can open
    the task and hit "Reassign" to route it to a different agent. When
    the task is no longer stuck (moved to done/cancelled or updated),
    the escalation pupdate is auto-dismissed.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.database import db

    STUCK_DAYS = 3
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=STUCK_DAYS)

    try:
        in_progress = Task.query.filter(Task.status == "in_progress").all()
        escalated = 0
        for t in in_progress:
            updated = t.updated_at
            if updated is None:
                continue
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if updated >= threshold:
                continue

            source_id = f"stuck-task/{t.id}"
            existing = Pupdate.query.filter_by(source_id=source_id).first()
            if existing:
                # Already surfaced once — respect the user's dismissal.
                # If they've decided they know about it, don't keep
                # reminding them every cycle. (And the pupdate ID is
                # fixed at `stuck-{task_id[:32]}`, so a second insert
                # would PK-conflict anyway.) The auto-dismiss pass
                # below still closes the pupdate when the task moves
                # out of in_progress.
                continue

            days = (now - updated).days
            agent_name = (t.assigned_agent_id or "unassigned")
            p = Pupdate(
                id=f"stuck-{t.id[:32]}",
                source="maiko",
                source_id=source_id,
                type="stuck_task",
                priority="high",
                title=f"Stuck task: {t.title}",
                body=f"Task has been in_progress for {days} days with no updates. Assigned to {agent_name}.",
                actionable=True,
                action_hint="Reassign or check in",
                tags=["stuck", t.id, agent_name],
                extra={"task_id": t.id, "days_stuck": days},
            )
            db.session.add(p)
            escalated += 1

        # Auto-dismiss escalations whose task is no longer stuck.
        dismissed = 0
        open_escalations = Pupdate.query.filter_by(type="stuck_task", dismissed=False).all()
        for p in open_escalations:
            task_id = (p.extra or {}).get("task_id")
            if not task_id:
                continue
            task = db.session.get(Task, task_id)
            if not task or task.status != "in_progress":
                # Task completed / cancelled / gone — close the escalation.
                p.dismissed = True
                p.dismissed_at = now
                dismissed += 1

        if escalated or dismissed:
            db.session.commit()
        return {"escalated": escalated, "auto_dismissed": dismissed}
    except Exception as e:
        logger.warning(f"[cycle] Stuck escalation skipped: {e}")
        return {"escalated": 0, "auto_dismissed": 0}
