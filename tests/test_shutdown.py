"""Tests for the shutdown/cleanup ritual.

Covers each STEPS[name] function against a fixture DB to assert the
right rows get pruned (and the wrong ones don't). stop_server is NOT
exercised — it SIGTERMs the process, which pytest obviously can't
survive.
"""

from datetime import datetime, timedelta, timezone

import pytest

from planet_maiko import shutdown
from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.models.insight import Insight
from planet_maiko.models.learning import Learning
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.signal import Signal
from planet_maiko.models.skill_result import SkillResult
from planet_maiko.models.task import Task


def _old(days=40):
    """UTC naive datetime N days ago — SQLite stores naive, so match."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)


def _recent(hours=1):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# prune_pupdates
# ---------------------------------------------------------------------------

def test_prune_pupdates_drops_old_read_processed_activity(app):
    with app.app_context():
        # Old, read, processed, activity → should prune
        db.session.add(Pupdate(
            id="prune-me", source="test", type="info", title="old", priority="normal",
            read=True, brain_processed=True, category="activity",
            timestamp=_old(days=2),
        ))
        # Old but unread → keep
        db.session.add(Pupdate(
            id="unread", source="test", type="info", title="unread", priority="normal",
            read=False, brain_processed=True, category="activity",
            timestamp=_old(days=2),
        ))
        # Recent → keep
        db.session.add(Pupdate(
            id="recent", source="test", type="info", title="recent", priority="normal",
            read=True, brain_processed=True, category="activity",
            timestamp=_recent(hours=1),
        ))
        # Actionable (category != activity) → keep
        db.session.add(Pupdate(
            id="action", source="test", type="pr_review_requested", title="action",
            priority="high", read=True, brain_processed=True, category="action",
            timestamp=_old(days=2),
        ))
        db.session.commit()

        result = shutdown.prune_pupdates()

    assert result["deleted"] == 1
    with app.app_context():
        remaining = {p.id for p in Pupdate.query.all()}
        assert remaining == {"unread", "recent", "action"}


# ---------------------------------------------------------------------------
# prune_messages
# ---------------------------------------------------------------------------

def test_prune_messages_only_done_tasks_old_messages(app):
    with app.app_context():
        done = Task(id="t-done", title="Done", status="done", updated_at=_old(days=30))
        active = Task(id="t-active", title="Active", status="in_progress",
                      updated_at=_old(days=30))
        db.session.add_all([done, active])
        db.session.flush()

        db.session.add_all([
            AgentMessage(task_id="t-done", direction="to_agent", sender="maiko",
                         content="old", created_at=_old(days=30)),
            AgentMessage(task_id="t-done", direction="from_agent", sender="agent",
                         content="recent", created_at=_recent(hours=1)),
            AgentMessage(task_id="t-active", direction="to_agent", sender="maiko",
                         content="active task, old msg", created_at=_old(days=30)),
        ])
        db.session.commit()

        result = shutdown.prune_messages()

    # Only the first message matches: old + task is done.
    assert result["deleted"] == 1
    with app.app_context():
        remaining = AgentMessage.query.count()
        assert remaining == 2


# ---------------------------------------------------------------------------
# prune_signals
# ---------------------------------------------------------------------------

def test_prune_signals_only_incorporated(app):
    with app.app_context():
        # Old + incorporated → prune
        db.session.add(Signal(
            category="testing", text="old+incorporated", source_type="pr_comment",
            incorporated_at=_old(days=120), created_at=_old(days=120),
        ))
        # Old but NOT incorporated → keep (not yet trained on)
        db.session.add(Signal(
            category="testing", text="old+uningested", source_type="pr_comment",
            incorporated_at=None, created_at=_old(days=120),
        ))
        # Incorporated but recent → keep
        db.session.add(Signal(
            category="testing", text="recent+incorporated", source_type="pr_comment",
            incorporated_at=_recent(hours=1), created_at=_recent(hours=1),
        ))
        db.session.commit()

        result = shutdown.prune_signals()

    assert result["deleted"] == 1
    with app.app_context():
        remaining = {s.text for s in Signal.query.all()}
        assert remaining == {"old+uningested", "recent+incorporated"}


# ---------------------------------------------------------------------------
# prune_skill_results
# ---------------------------------------------------------------------------

def test_prune_skill_results_by_age(app):
    with app.app_context():
        db.session.add(SkillResult(
            skill_name="morning-brief", title="old", content="x",
            created_at=_old(days=60),
        ))
        db.session.add(SkillResult(
            skill_name="morning-brief", title="recent", content="x",
            created_at=_recent(hours=1),
        ))
        db.session.commit()

        result = shutdown.prune_skill_results()

    assert result["deleted"] == 1
    with app.app_context():
        assert SkillResult.query.count() == 1


# ---------------------------------------------------------------------------
# prune_dismissed
# ---------------------------------------------------------------------------

def test_prune_dismissed_insights_and_learnings(app):
    with app.app_context():
        db.session.add(Insight(
            text="dismissed long ago", status="dismissed",
            created_at=_old(days=60), updated_at=_old(days=60),
        ))
        db.session.add(Insight(
            text="active, keep", status="active",
            created_at=_old(days=60), updated_at=_old(days=60),
        ))
        db.session.add(Learning(
            rule="dismissed learning", category="pattern", status="dismissed",
            created_at=_old(days=60), updated_at=_old(days=60),
        ))
        db.session.commit()

        result = shutdown.prune_dismissed()

    assert result["insights"] == 1
    assert result["learnings"] == 1
    with app.app_context():
        assert Insight.query.count() == 1
        assert Learning.query.count() == 0


# ---------------------------------------------------------------------------
# stop_active_agents — message-queue side effect
# ---------------------------------------------------------------------------

def test_stop_active_agents_queues_message_for_each_active_worktree(app):
    with app.app_context():
        db.session.add(Task(
            id="t-active", title="Active", status="in_progress",
            extra={"working_path": "/tmp/wt1"},
        ))
        db.session.add(Task(
            id="t-no-wt", title="No worktree", status="in_progress",
            extra={},
        ))
        db.session.add(Task(
            id="t-done", title="Done", status="done",
            extra={"working_path": "/tmp/wt2"},
        ))
        db.session.commit()

        result = shutdown.stop_active_agents()

    assert result["stopped"] == 1
    with app.app_context():
        msgs = AgentMessage.query.filter_by(message_type="shutdown").all()
        assert len(msgs) == 1
        assert msgs[0].task_id == "t-active"


# ---------------------------------------------------------------------------
# preview — count contract
# ---------------------------------------------------------------------------

def test_preview_returns_expected_keys(app):
    with app.app_context():
        preview = shutdown.preview()
    expected = {
        "active_sessions", "worktrees", "pupdates", "agent_messages",
        "signals", "skill_results", "dismissed",
    }
    assert expected.issubset(set(preview.keys()))
