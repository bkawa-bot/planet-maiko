"""Tests for focus mode — state management, calendar-based auto-focus, gating."""

import pytest
from datetime import datetime, timezone, timedelta
from planet_maiko.brain.focus import manager as focus_mgr
from planet_maiko.models.pupdate import Pupdate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_focus_state():
    """Reset the module-level focus state before each test."""
    focus_mgr._state.update({
        "current_state": "available",
        "entered_at": None,
        "trigger": None,
        "duration_minutes": None,
        "expires_at": None,
        "held_count": 0,
    })


# ---------------------------------------------------------------------------
# set_state / get_state
# ---------------------------------------------------------------------------


def test_set_focus_deep_focus_changes_state():
    result = focus_mgr.set_state("deep_focus")
    assert result["current_state"] == "deep_focus"
    assert result["trigger"] == "explicit"


def test_set_focus_soft_focus():
    result = focus_mgr.set_state("soft_focus")
    assert result["current_state"] == "soft_focus"


def test_set_focus_away():
    result = focus_mgr.set_state("away")
    assert result["current_state"] == "away"


def test_set_focus_available(app, db):
    focus_mgr.set_state("deep_focus")
    result = focus_mgr.set_state("available")
    assert result["current_state"] == "available"


def test_set_focus_invalid_state_raises():
    with pytest.raises(ValueError, match="Invalid state"):
        focus_mgr.set_state("invalid_state")


def test_get_focus_returns_current_state():
    focus_mgr.set_state("soft_focus")
    state = focus_mgr.get_state()
    assert state["current_state"] == "soft_focus"


def test_set_focus_records_entered_at():
    focus_mgr.set_state("deep_focus")
    state = focus_mgr.get_state()
    assert state["entered_at"] is not None


def test_set_focus_with_duration():
    focus_mgr.set_state("deep_focus", duration_minutes=30)
    state = focus_mgr.get_state()
    assert state["duration_minutes"] == 30
    assert state["expires_at"] is not None


# ---------------------------------------------------------------------------
# should_surface
# ---------------------------------------------------------------------------


def test_should_surface_everything_when_available(app, db):
    p = Pupdate(
        id="test-normal", source="test", type="info",
        priority="normal", title="Normal update",
    )
    db.session.add(p)
    db.session.commit()

    assert focus_mgr.should_surface(p) is True


def test_should_surface_blocks_normal_in_deep_focus(app, db):
    focus_mgr.set_state("deep_focus")

    p = Pupdate(
        id="test-blocked", source="test", type="info",
        priority="normal", title="Normal update",
    )
    db.session.add(p)
    db.session.commit()

    assert focus_mgr.should_surface(p) is False


def test_should_surface_allows_critical_in_deep_focus(app, db):
    focus_mgr.set_state("deep_focus")

    p = Pupdate(
        id="test-critical", source="test", type="deploy_rollback",
        priority="normal", title="Deploy rollback",
    )
    db.session.add(p)
    db.session.commit()

    # deploy_rollback is in CRITICAL_TYPES, always surfaces
    assert focus_mgr.should_surface(p) is True


def test_should_surface_allows_high_in_soft_focus(app, db):
    focus_mgr.set_state("soft_focus")

    p = Pupdate(
        id="test-high", source="test", type="info",
        priority="high", title="High priority update",
    )
    db.session.add(p)
    db.session.commit()

    assert focus_mgr.should_surface(p) is True


def test_should_surface_blocks_low_in_soft_focus(app, db):
    focus_mgr.set_state("soft_focus")

    p = Pupdate(
        id="test-low-blocked", source="test", type="info",
        priority="low", title="Low priority update",
    )
    db.session.add(p)
    db.session.commit()

    assert focus_mgr.should_surface(p) is False


# ---------------------------------------------------------------------------
# check_calendar_focus
# ---------------------------------------------------------------------------


def test_check_calendar_focus_sets_soft_focus_when_meeting_soon(app, db):
    now = datetime.now(timezone.utc)
    meeting_start = (now + timedelta(minutes=3)).isoformat()

    p = Pupdate(
        id="cal-meeting", source="calendar", type="calendar_event",
        priority="normal", title="Team standup",
        extra={"start": meeting_start},
    )
    db.session.add(p)
    db.session.commit()

    result = focus_mgr.check_calendar_focus([p])
    assert result is True
    assert focus_mgr.get_state()["current_state"] == "soft_focus"


def test_check_calendar_focus_returns_to_available_when_meeting_ends(app, db):
    now = datetime.now(timezone.utc)
    # Meeting that started 31 minutes ago and ended 1 minute ago
    meeting_start = (now - timedelta(minutes=31)).isoformat()
    meeting_end = (now - timedelta(minutes=1)).isoformat()

    # Set initial state to soft_focus (as if auto-set when meeting started)
    focus_mgr.set_state("soft_focus", trigger="calendar")

    p = Pupdate(
        id="cal-ended", source="calendar", type="calendar_event",
        priority="normal", title="Team standup",
        extra={"start": meeting_start, "end": meeting_end},
    )
    db.session.add(p)
    db.session.commit()

    result = focus_mgr.check_calendar_focus([p])
    assert result is True
    assert focus_mgr.get_state()["current_state"] == "available"


def test_check_calendar_focus_ignores_non_calendar_pupdates(app, db):
    p = Pupdate(
        id="not-cal", source="github", type="pr_opened",
        priority="normal", title="PR opened",
    )
    db.session.add(p)
    db.session.commit()

    result = focus_mgr.check_calendar_focus([p])
    assert result is False
    assert focus_mgr.get_state()["current_state"] == "available"


def test_check_calendar_focus_does_not_override_explicit_deep_focus(app, db):
    now = datetime.now(timezone.utc)
    meeting_start = (now + timedelta(minutes=3)).isoformat()

    focus_mgr.set_state("deep_focus", trigger="explicit")

    p = Pupdate(
        id="cal-during-deep", source="calendar", type="calendar_event",
        priority="normal", title="Meeting during deep focus",
        extra={"start": meeting_start},
    )
    db.session.add(p)
    db.session.commit()

    # Should not downgrade deep_focus to soft_focus
    result = focus_mgr.check_calendar_focus([p])
    assert result is False
    assert focus_mgr.get_state()["current_state"] == "deep_focus"


def test_check_calendar_focus_does_not_trigger_for_distant_meeting(app, db):
    now = datetime.now(timezone.utc)
    # Meeting starting in 30 minutes -- too far out
    meeting_start = (now + timedelta(minutes=30)).isoformat()

    p = Pupdate(
        id="cal-far", source="calendar", type="calendar_event",
        priority="normal", title="Future meeting",
        extra={"start": meeting_start},
    )
    db.session.add(p)
    db.session.commit()

    result = focus_mgr.check_calendar_focus([p])
    assert result is False
    assert focus_mgr.get_state()["current_state"] == "available"
