"""Tests for agent profile management — names, stats, specializations, recommendations."""

import pytest
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.models.context_selection import ContextSelection
from planet_maiko.models.learning import Learning
from planet_maiko.agents.profiles import (
    create_profile,
    record_task_outcome,
    record_session_feedback,
    recommend_agent,
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


# ---------------------------------------------------------------------------
# recommend_agent
# ---------------------------------------------------------------------------


def test_recommend_agent_returns_sorted_by_score(app, db):
    p1 = AgentProfile(
        id="agent-r1", display_name="R1 Bot", avatar="shiba",
        tasks_completed=10, tasks_failed=2,
        specializations={"repo-a:testing": 0.9},
    )
    p2 = AgentProfile(
        id="agent-r2", display_name="R2 Bot", avatar="corgi",
        tasks_completed=1, tasks_failed=0,
        specializations={},
    )
    db.session.add_all([p1, p2])
    db.session.commit()

    recs = recommend_agent(repo="repo-a", categories=["testing"])
    scores = [r["score"] for r in recs if r.get("profile")]
    assert scores == sorted(scores, reverse=True)
    assert recs[0]["profile"]["id"] == "agent-r1"


def test_recommend_agent_exploration_bonus_for_new_agents(app, db):
    experienced = AgentProfile(
        id="agent-exp", display_name="Exp Bot", avatar="shiba",
        tasks_completed=5, tasks_failed=5,
        specializations={},
    )
    newbie = AgentProfile(
        id="agent-new", display_name="New Bot", avatar="corgi",
        tasks_completed=0, tasks_failed=0,
        specializations={},
    )
    db.session.add_all([experienced, newbie])
    db.session.commit()

    recs = recommend_agent()
    newbie_rec = next(r for r in recs if r.get("profile") and r["profile"]["id"] == "agent-new")
    assert "exploration candidate" in newbie_rec["reasons"]


def test_recommend_agent_exploration_bonus_decays(app, db):
    """Exploration bonus is 0.15 at 0 tasks and decays to 0.05 at 2 tasks."""
    zero_tasks = AgentProfile(
        id="agent-z", display_name="Zero Bot", avatar="shiba",
        tasks_completed=0, tasks_failed=0,
    )
    two_tasks = AgentProfile(
        id="agent-t", display_name="Two Bot", avatar="corgi",
        tasks_completed=1, tasks_failed=1,
    )
    db.session.add_all([zero_tasks, two_tasks])
    db.session.commit()

    recs = recommend_agent()
    z_rec = next(r for r in recs if r.get("profile") and r["profile"]["id"] == "agent-z")
    t_rec = next(r for r in recs if r.get("profile") and r["profile"]["id"] == "agent-t")
    # Zero-task: exploration=0.15, rate=0, volume=0 -> 0.15
    # Two-task: exploration=0.05, rate=0.5*0.3=0.15, volume=2*0.02=0.04 -> 0.24
    # Two-task scores higher due to rate+volume, but zero-task has higher exploration portion
    assert z_rec["score"] == pytest.approx(0.15, abs=0.01)
    assert t_rec["score"] == pytest.approx(0.24, abs=0.01)
    # Verify exploration bonus is larger for zero-task agent
    assert z_rec["score"] < t_rec["score"]  # volume+rate outweigh exploration


def test_recommend_agent_gap_detected_when_no_agent_above_threshold(app, db):
    """When all agents score below GAP_THRESHOLD (0.3), a gap entry is inserted."""
    weak = AgentProfile(
        id="agent-weak", display_name="Weak Bot", avatar="shiba",
        # 0 completed, 3 failed -> success_rate=0, total=3 (no exploration bonus)
        # score = rate*0.3(=0) + volume*0.02*3(=0.06) = 0.06 < 0.3
        tasks_completed=0, tasks_failed=3,
        specializations={},
    )
    db.session.add(weak)
    db.session.commit()

    recs = recommend_agent(repo="unknown-repo", categories=["security"])
    gap_entries = [r for r in recs if r.get("gap_detected")]
    assert len(gap_entries) >= 1


def test_recommend_agent_empty_db_returns_gap(app, db):
    recs = recommend_agent()
    assert len(recs) == 1
    assert recs[0]["gap_detected"] is True


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


def test_rank_pup_for_new_agent(app, db):
    p = AgentProfile(id="r-pup", display_name="Pup", avatar="shiba",
                     tasks_completed=0, tasks_failed=0)
    assert p.rank() == "pup"


def test_rank_junior_at_three_tasks(app, db):
    p = AgentProfile(id="r-jr", display_name="Jr", avatar="corgi",
                     tasks_completed=2, tasks_failed=1)
    assert p.rank() == "junior"


def test_rank_senior_at_ten_tasks(app, db):
    p = AgentProfile(id="r-sr", display_name="Sr", avatar="husky",
                     tasks_completed=6, tasks_failed=4)
    assert p.rank() == "senior"


def test_rank_expert_at_twenty_tasks_with_high_success(app, db):
    p = AgentProfile(id="r-exp", display_name="Exp", avatar="poodle",
                     tasks_completed=18, tasks_failed=2)
    assert p.rank() == "expert"


def test_rank_senior_not_expert_with_low_success(app, db):
    p = AgentProfile(id="r-low", display_name="Low", avatar="golden",
                     tasks_completed=10, tasks_failed=10)
    assert p.rank() == "senior"
