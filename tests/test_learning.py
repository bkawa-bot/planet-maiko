"""Tests for the learning pipeline — signal aggregation, graduation."""

import pytest
from planet_maiko.models.signal import Signal
from planet_maiko.models.learning import Learning
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.brain.learning.processor import process_signals


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


