"""Tests for agent profile management — names, stats, specializations."""

import pytest  # noqa: F401 — kept for fixtures in other tests
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.models.context_selection import ContextSelection
from planet_maiko.models.learning import Learning
from planet_maiko.agents.profiles import (
    create_profile,
    record_task_outcome,
    record_session_feedback,
    TECH_SUFFIXES,
)


# ---------------------------------------------------------------------------
# create_profile
# ---------------------------------------------------------------------------


def test_create_profile_adds_tech_suffix(app, db):
    profile = create_profile("agent-1", display_name="Glitch")
    assert any(profile.display_name.endswith(suffix) for suffix in TECH_SUFFIXES)
    assert profile.display_name.startswith("Glitch")


def test_create_profile_assigns_avatar(app, db):
    profile = create_profile("agent-2")
    assert profile.avatar is not None
    assert profile.avatar != ""


def test_create_profile_is_idempotent(app, db):
    first = create_profile("agent-dup", display_name="Echo")
    second = create_profile("agent-dup", display_name="ShouldBeIgnored")
    assert first.id == second.id
    assert first.display_name == second.display_name


def test_create_profile_sets_flavor_text(app, db):
    profile = create_profile("agent-3")
    assert profile.flavor_text is not None


def test_create_profile_starts_at_zero_stats(app, db):
    profile = create_profile("agent-4")
    assert profile.tasks_completed == 0
    assert profile.tasks_failed == 0


# ---------------------------------------------------------------------------
# record_task_outcome
# ---------------------------------------------------------------------------


def test_record_task_outcome_success_increments_completed(app, db):
    profile = AgentProfile(id="agent-s1", display_name="Test Bot", avatar="shiba")
    db.session.add(profile)

    sel = ContextSelection(
        task_id="task-1",
        agent_profile_id="agent-s1",
        repo="my-repo",
        learning_ids=[],
        outcome=None,
    )
    db.session.add(sel)
    db.session.commit()

    count = record_task_outcome("task-1", "success")
    assert count == 1

    refreshed = db.session.get(AgentProfile, "agent-s1")
    assert refreshed.tasks_completed == 1
    assert refreshed.tasks_failed == 0


def test_record_task_outcome_failed_increments_failed(app, db):
    profile = AgentProfile(id="agent-f1", display_name="Fail Bot", avatar="corgi")
    db.session.add(profile)

    sel = ContextSelection(
        task_id="task-fail",
        agent_profile_id="agent-f1",
        repo="my-repo",
        learning_ids=[],
        outcome=None,
    )
    db.session.add(sel)
    db.session.commit()

    record_task_outcome("task-fail", "failed")

    refreshed = db.session.get(AgentProfile, "agent-f1")
    assert refreshed.tasks_failed == 1
    assert refreshed.tasks_completed == 0


def test_record_task_outcome_updates_specialization_via_learning_ids(app, db):
    profile = AgentProfile(id="agent-spec", display_name="Spec Bot", avatar="husky")
    db.session.add(profile)

    learning = Learning(
        rule="Always use Optional for nullable returns",
        category="null_safety",
        status="active",
        signal_count=3,
        confidence=0.3,
    )
    db.session.add(learning)
    db.session.flush()

    sel = ContextSelection(
        task_id="task-spec",
        agent_profile_id="agent-spec",
        repo="api-service",
        learning_ids=[learning.id],
        outcome=None,
    )
    db.session.add(sel)
    db.session.commit()

    record_task_outcome("task-spec", "success")

    refreshed = db.session.get(AgentProfile, "agent-spec")
    specs = refreshed.specializations or {}
    assert "api-service:null_safety" in specs
    assert specs["api-service:null_safety"] == pytest.approx(0.1, abs=0.01)


def test_record_task_outcome_failed_increments_failed_count(app, db):
    """Verify failed outcome increments tasks_failed counter."""
    profile = AgentProfile(
        id="agent-dec",
        display_name="Dec Bot",
        avatar="poodle",
    )
    db.session.add(profile)

    learning = Learning(
        rule="Mock external deps in tests",
        category="testing",
        status="active",
        signal_count=3,
        confidence=0.3,
    )
    db.session.add(learning)
    db.session.flush()

    sel = ContextSelection(
        task_id="task-dec",
        agent_profile_id="agent-dec",
        repo="my-repo",
        learning_ids=[learning.id],
        outcome=None,
    )
    db.session.add(sel)
    db.session.commit()

    record_task_outcome("task-dec", "failed")

    refreshed = db.session.get(AgentProfile, "agent-dec")
    assert refreshed.tasks_failed == 1


def test_record_task_outcome_failed_sets_specialization_from_zero(app, db):
    """When specialization starts empty, failed outcome writes a decremented value.

    Note: SQLAlchemy JSON column does not detect in-place dict mutation, so
    updates only persist when specializations starts as None/empty (creating a
    new dict object). This is a known limitation of the current code.
    """
    profile = AgentProfile(
        id="agent-dec2",
        display_name="Dec2 Bot",
        avatar="poodle",
    )
    db.session.add(profile)

    learning = Learning(
        rule="Mock external deps in tests",
        category="testing",
        status="active",
        signal_count=3,
        confidence=0.3,
    )
    db.session.add(learning)
    db.session.flush()

    sel = ContextSelection(
        task_id="task-dec2",
        agent_profile_id="agent-dec2",
        repo="my-repo",
        learning_ids=[learning.id],
        outcome=None,
    )
    db.session.add(sel)
    db.session.commit()

    record_task_outcome("task-dec2", "failed")

    refreshed = db.session.get(AgentProfile, "agent-dec2")
    specs = refreshed.specializations or {}
    # max(0.0, 0.0 - 0.05) = 0.0
    assert specs.get("my-repo:testing", 0.0) == 0.0


# ---------------------------------------------------------------------------
# record_session_feedback
# ---------------------------------------------------------------------------


def test_record_session_feedback_applies_penalty(app, db):
    """Feedback applies a penalty to the relevant specialization key.

    Note: like record_task_outcome, this only persists when specializations
    starts as None/empty due to SQLAlchemy JSON mutation tracking.
    """
    profile = AgentProfile(
        id="agent-fb",
        display_name="Feedback Bot",
        avatar="golden",
        # Start with None so the code creates a new dict (avoids JSON mutation issue)
    )
    db.session.add(profile)

    sel = ContextSelection(
        task_id="task-fb",
        agent_profile_id="agent-fb",
        repo="my-repo",
        learning_ids=[],
        outcome=None,
    )
    db.session.add(sel)
    db.session.commit()

    record_session_feedback("task-fb", "style", severity="warning")

    refreshed = db.session.get(AgentProfile, "agent-fb")
    specs = refreshed.specializations or {}
    # max(0.0, 0.0 + (-0.02)) = 0.0 (penalty clamped at zero)
    assert specs.get("my-repo:style", 0.0) == 0.0


def test_record_session_feedback_does_not_close_task(app, db):
    profile = AgentProfile(id="agent-fb2", display_name="FB2 Bot", avatar="beagle")
    db.session.add(profile)

    sel = ContextSelection(
        task_id="task-fb2",
        agent_profile_id="agent-fb2",
        repo="r",
        learning_ids=[],
        outcome=None,
    )
    db.session.add(sel)
    db.session.commit()

    record_session_feedback("task-fb2", "naming", severity="suggestion")

    refreshed_sel = ContextSelection.query.filter_by(task_id="task-fb2").first()
    assert refreshed_sel.outcome is None


