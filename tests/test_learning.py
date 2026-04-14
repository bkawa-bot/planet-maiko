"""Tests for the learning pipeline — signal aggregation, graduation, brief compilation."""

import pytest
from planet_maiko.models.signal import Signal
from planet_maiko.models.learning import Learning
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.models.context_selection import ContextSelection
from planet_maiko.brain.learning.processor import (
    process_signals,
    compile_brief,
)


# ---------------------------------------------------------------------------
# process_signals — aggregation
# ---------------------------------------------------------------------------


def test_process_signals_creates_learning_from_signal(app, db):
    sig = Signal(
        category="null_safety",
        text="Always check for null before dereferencing",
        source_type="pr_comment",
        repo="api-service",
        language="java",
    )
    db.session.add(sig)
    db.session.commit()

    result = process_signals()
    assert result["processed"] == 1
    assert result["new_learnings"] == 1

    learning = Learning.query.filter_by(category="null_safety").first()
    assert learning is not None
    assert learning.rule == sig.text
    assert learning.category == "null_safety"
    assert learning.signal_count == 1
    assert learning.status == "pending"


def test_process_signals_aggregates_duplicate_signals(app, db):
    for i in range(3):
        sig = Signal(
            category="error_handling",
            text="Always set timeouts on HTTP requests",
            source_type="pr_comment",
            repo="api-service",
            language="python",
        )
        db.session.add(sig)
    db.session.commit()

    result = process_signals()
    assert result["processed"] == 3
    assert result["new_learnings"] == 1
    assert result["updated_learnings"] == 2

    learning = Learning.query.first()
    assert learning.signal_count == 3


def test_process_signals_never_auto_graduates(app, db):
    # Auto-graduation was removed — every Learning stays "pending"
    # until the user approves it, regardless of signal_count or
    # category. This test asserts that invariant across several
    # categories that used to have different thresholds.
    for category in ("error_handling", "security", "style"):
        for i in range(5):
            db.session.add(Signal(
                category=category,
                text=f"Rule for {category}",
                source_type="pr_comment",
                repo="api-service",
                language="python",
            ))
    db.session.commit()

    result = process_signals()
    assert result["graduated"] == 0

    for l in Learning.query.all():
        assert l.status == "pending", f"{l.category} Learning should stay pending"
        assert l.signal_count >= 5


def test_process_signals_noop_when_no_signals(app, db):
    result = process_signals()
    assert result["processed"] == 0
    assert result["new_learnings"] == 0


def test_process_signals_marks_signals_aggregated(app, db):
    sig = Signal(
        category="style",
        text="Keep functions under 30 lines",
        source_type="manual",
    )
    db.session.add(sig)
    db.session.commit()

    process_signals()

    refreshed = Signal.query.first()
    assert refreshed.aggregated is True


def test_process_signals_links_signal_to_learning(app, db):
    sig = Signal(
        category="testing",
        text="Use parameterized tests for input variation",
        source_type="pr_comment",
    )
    db.session.add(sig)
    db.session.commit()

    process_signals()

    refreshed = Signal.query.first()
    assert refreshed.learning_id is not None
    assert db.session.get(Learning, refreshed.learning_id) is not None


# ---------------------------------------------------------------------------
# compile_brief
# ---------------------------------------------------------------------------


def test_compile_brief_returns_markdown_with_active_learnings(app, db):
    l1 = Learning(
        rule="Always use Optional for nullable returns",
        category="null_safety",
        status="active",
        confidence=0.5,
        signal_count=5,
    )
    l2 = Learning(
        rule="Wrap API calls in try/except",
        category="error_handling",
        status="active",
        confidence=0.3,
        signal_count=3,
    )
    db.session.add_all([l1, l2])
    db.session.commit()

    brief = compile_brief()
    assert "# Coding Guidelines (Learned)" in brief
    assert "Optional" in brief
    assert "try/except" in brief


def test_compile_brief_excludes_pending_learnings(app, db):
    active = Learning(
        rule="Active rule", category="testing",
        status="active", confidence=0.5, signal_count=5,
    )
    pending = Learning(
        rule="Pending rule should NOT appear", category="style",
        status="pending", confidence=0.1, signal_count=1,
    )
    db.session.add_all([active, pending])
    db.session.commit()

    brief = compile_brief()
    assert "Active rule" in brief
    assert "Pending rule should NOT appear" not in brief


def test_compile_brief_records_context_selection_when_task_id_provided(app, db):
    learning = Learning(
        rule="Use fixtures for test data",
        category="testing",
        status="active",
        confidence=0.5,
        signal_count=5,
    )
    db.session.add(learning)
    db.session.commit()

    compile_brief(task_id="task-brief-1", agent_profile_id="agent-x")

    sel = ContextSelection.query.filter_by(task_id="task-brief-1").first()
    assert sel is not None
    assert sel.agent_profile_id == "agent-x"
    assert learning.id in sel.learning_ids


def test_compile_brief_does_not_record_selection_without_task_id(app, db):
    learning = Learning(
        rule="A rule", category="testing",
        status="active", confidence=0.5, signal_count=5,
    )
    db.session.add(learning)
    db.session.commit()

    compile_brief()

    count = ContextSelection.query.count()
    assert count == 0


def test_compile_brief_includes_exploration_slots(app, db):
    # Create enough learnings that exploration slots are meaningful
    for i in range(10):
        learning = Learning(
            rule=f"Rule number {i}",
            category="testing",
            status="active",
            confidence=0.5 - i * 0.02,
            signal_count=5,
        )
        db.session.add(learning)
    db.session.commit()

    brief = compile_brief(max_learnings=8)
    # Should include rules beyond the top 6 (8 - 2 exploration slots)
    assert "# Coding Guidelines (Learned)" in brief


def test_compile_brief_returns_no_learnings_message_when_empty(app, db):
    brief = compile_brief()
    assert brief == "No active learnings yet."


def test_compile_brief_scopes_to_repo(app, db):
    global_learning = Learning(
        rule="Global rule applies everywhere",
        category="style",
        status="active",
        confidence=0.5,
        signal_count=5,
        scope_repo=None,
    )
    repo_learning = Learning(
        rule="Repo-specific rule for api-service",
        category="testing",
        status="active",
        confidence=0.5,
        signal_count=5,
        scope_repo="api-service",
    )
    other_repo_learning = Learning(
        rule="Should not appear for api-service",
        category="docs",
        status="active",
        confidence=0.5,
        signal_count=5,
        scope_repo="other-repo",
    )
    db.session.add_all([global_learning, repo_learning, other_repo_learning])
    db.session.commit()

    brief = compile_brief(repo="api-service")
    assert "Global rule" in brief
    assert "Repo-specific rule" in brief
    assert "Should not appear" not in brief
