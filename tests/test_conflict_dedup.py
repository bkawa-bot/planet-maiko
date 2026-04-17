"""Tests for conflict-detection dedup.

The pupdate stream was growing by N per cycle for as long as two
agents edited the same file — each cycle minted a new UUID-suffixed
row. These tests lock in the fix: deterministic pupdate IDs, stable
source_ids, and DB-backed dedup replacing the old in-memory set.
"""

from datetime import datetime, timedelta, timezone

import pytest

from planet_maiko.brain.awareness import conflicts as c
from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate


def test_conflict_key_is_order_independent():
    """Reordering the agents must produce the same dedup key. The old
    code hashed `{agent_a}:{agent_b}:{file}` which was order-dependent.
    """
    k1 = c._conflict_key(["agent-mochi", "agent-luna"], "src/foo.py")
    k2 = c._conflict_key(["agent-luna", "agent-mochi"], "src/foo.py")
    assert k1 == k2


def test_pupdate_id_fits_in_column():
    """Pupdate.id is VARCHAR(64) — our deterministic id must fit."""
    key = c._conflict_key(["agent-very-very-long-name", "agent-also-long"], "src/deeply/nested/module.py")
    pid = c._pupdate_id("escalation", key)
    assert len(pid) <= 64


def test_send_conflict_warnings_skips_when_already_escalated(app):
    """Second call on the same conflict should be a no-op (no new
    AgentMessage rows, no new pupdates). This is the core anti-spam
    behavior.
    """
    from planet_maiko.models.agent_message import AgentMessage

    conflict = {
        "agents": ["agent-a", "agent-b"],
        "file": "src/foo.py",
        "severity": "hard",
        "overlapping_methods": ["do_thing"],
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }

    with app.app_context():
        # First detection: warnings fire + a warning pupdate lands.
        c.send_conflict_warnings([conflict])
        first_msgs = AgentMessage.query.filter_by(message_type="conflict_warning").count()
        first_pups = Pupdate.query.filter_by(type="conflict_warning").count()

        # Second detection a cycle later: same conflict, same key.
        c.send_conflict_warnings([conflict])
        second_msgs = AgentMessage.query.filter_by(message_type="conflict_warning").count()
        second_pups = Pupdate.query.filter_by(type="conflict_warning").count()

    assert first_msgs == 2, "first pass should warn both agents"
    assert first_pups == 1, "first pass should create one tracking pupdate"
    assert second_msgs == first_msgs, "second pass should not add new messages"
    assert second_pups == first_pups, "second pass should not add new pupdates"


def test_already_escalated_respects_dismissal_grace_window(app):
    """If the user dismissed an escalation recently, _already_escalated
    returns True so we don't re-open it. After the grace window,
    re-detection is allowed (the overlap may have morphed in a way
    worth surfacing again).
    """
    key = c._conflict_key(["agent-a", "agent-b"], "src/foo.py")

    with app.app_context():
        # Dismissed 1 hour ago — still inside the 6-hour grace window
        recent = Pupdate(
            id=c._pupdate_id("escalation", key),
            source="maiko",
            source_id=c._source_id(key),
            type="conflict_escalation",
            priority="high",
            title="old conflict",
            dismissed=True,
            dismissed_at=(datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None),
        )
        db.session.add(recent)
        db.session.commit()
        assert c._already_escalated(key) is True

        # Dismissed 12 hours ago — past the grace window
        recent.dismissed_at = (datetime.now(timezone.utc) - timedelta(hours=12)).replace(tzinfo=None)
        db.session.commit()
        assert c._already_escalated(key) is False


def test_already_escalated_treats_active_pupdate_as_escalated(app):
    """Undismissed escalation = already on the user's plate, skip."""
    key = c._conflict_key(["agent-a", "agent-b"], "src/foo.py")
    with app.app_context():
        db.session.add(Pupdate(
            id=c._pupdate_id("escalation", key),
            source="maiko",
            source_id=c._source_id(key),
            type="conflict_escalation",
            priority="high",
            title="active conflict",
            dismissed=False,
        ))
        db.session.commit()
        assert c._already_escalated(key) is True


def test_already_escalated_returns_false_when_missing(app):
    with app.app_context():
        assert c._already_escalated("never-seen|src/foo.py") is False
