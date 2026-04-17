"""Tests for the stuck-task escalation flow — our first line of
defense when an agent subprocess dies quietly.

We can't actually kill Claude CLI processes from pytest (nor should
we), but we CAN exercise the downstream flow that notices something
went wrong: task still in_progress, no updates for N days → the
cycle emits a high-priority stuck_task pupdate the user can act on.
The agent-crash UX hinges on that pupdate firing reliably.
"""

from datetime import datetime, timedelta, timezone

import pytest

from planet_maiko.brain.cycle import _phase_stuck_escalation
from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task


def _naive(dt):
    """SQLite stores naive datetimes — strip tz for DB writes."""
    return dt.replace(tzinfo=None)


def test_stuck_in_progress_task_gets_escalation_pupdate(app):
    """Task in_progress whose updated_at is > STUCK_DAYS old fires a
    stuck_task pupdate. This is how an agent crash surfaces: the
    agent stops updating the task, cycle eventually notices.
    """
    with app.app_context():
        db.session.add(Task(
            id="task-crashed",
            title="Crashed mid-work",
            status="in_progress",
            assigned_agent_id="agent-mochi",
            updated_at=_naive(datetime.now(timezone.utc) - timedelta(days=5)),
        ))
        db.session.commit()

        _phase_stuck_escalation()

        pupdates = Pupdate.query.filter_by(type="stuck_task").all()
        assert len(pupdates) == 1
        p = pupdates[0]
        assert p.priority == "high"
        assert p.actionable is True
        assert (p.extra or {}).get("task_id") == "task-crashed"
        assert "agent-mochi" in (p.body or "")


def test_fresh_in_progress_task_does_not_escalate(app):
    """An active task with recent updates shouldn't fire an
    escalation — would be false positives during legitimate work.
    """
    with app.app_context():
        db.session.add(Task(
            id="task-active",
            title="Still working",
            status="in_progress",
            updated_at=_naive(datetime.now(timezone.utc) - timedelta(hours=2)),
        ))
        db.session.commit()

        _phase_stuck_escalation()

        assert Pupdate.query.filter_by(type="stuck_task").count() == 0


def test_stuck_escalation_is_idempotent(app):
    """Running the phase twice on the same stuck task doesn't
    create a second pupdate. Important — the cycle runs every 5
    minutes; we don't want duplicate noise every tick.
    """
    with app.app_context():
        db.session.add(Task(
            id="task-once",
            title="Stuck",
            status="in_progress",
            updated_at=_naive(datetime.now(timezone.utc) - timedelta(days=5)),
        ))
        db.session.commit()

        _phase_stuck_escalation()
        _phase_stuck_escalation()
        _phase_stuck_escalation()

        assert Pupdate.query.filter_by(type="stuck_task").count() == 1


def test_escalation_auto_dismisses_when_task_leaves_in_progress(app):
    """User reassigns or completes the task → escalation goes away.
    Recovery path closes itself — no stale "stuck" pupdates sitting
    around after the user took action.
    """
    with app.app_context():
        db.session.add(Task(
            id="task-rescued",
            title="Was stuck",
            status="in_progress",
            updated_at=_naive(datetime.now(timezone.utc) - timedelta(days=5)),
        ))
        db.session.commit()

        _phase_stuck_escalation()
        assert Pupdate.query.filter_by(type="stuck_task", dismissed=False).count() == 1

        # User finishes the task
        t = db.session.get(Task, "task-rescued")
        t.status = "done"
        db.session.commit()

        _phase_stuck_escalation()

        active = Pupdate.query.filter_by(type="stuck_task", dismissed=False).count()
        dismissed = Pupdate.query.filter_by(type="stuck_task", dismissed=True).count()
        assert active == 0
        assert dismissed == 1


def test_dismissed_escalation_does_not_refire_while_still_stuck(app):
    """Escalation matched on source_id, not just type. Once a user
    dismisses a stuck_task pupdate manually, the phase shouldn't
    re-create it next cycle — they know, they're handling it.
    """
    with app.app_context():
        db.session.add(Task(
            id="task-known-stuck",
            title="Stuck, user is on it",
            status="in_progress",
            updated_at=_naive(datetime.now(timezone.utc) - timedelta(days=5)),
        ))
        db.session.commit()

        _phase_stuck_escalation()
        p = Pupdate.query.filter_by(type="stuck_task").first()
        assert p is not None

        # User dismisses manually
        p.dismissed = True
        p.dismissed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()

        # Next cycle shouldn't re-fire
        _phase_stuck_escalation()
        active = Pupdate.query.filter_by(type="stuck_task", dismissed=False).count()
        assert active == 0
