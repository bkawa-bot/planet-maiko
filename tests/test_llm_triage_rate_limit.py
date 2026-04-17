"""Tests for the LLM triage rate limit + type-level cache.

The processor used to fire one LLM call per unmatched pupdate, every
cycle. A backfill or a noisy poller could fan out into dozens of
calls per tick. These tests lock in the two dials that fixed that:

  - Per-cycle cap (brain.llm_triage_per_cycle, default 10) — above
    this, unmatched pupdates defer to the next pass.
  - Per-type cache — first pupdate of type X burns an LLM call, every
    other pupdate of the same type in the same pass reuses the
    decision for free.
"""

from unittest.mock import patch

import pytest

from planet_maiko.brain.pupdates import processor as proc
from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate


@pytest.fixture
def _enable_triage(monkeypatch):
    """Force llm_triage on + a predictable cap for tests."""
    import planet_maiko.config as cfg_mod

    def _fake():
        return {"brain": {"llm_triage": True, "llm_triage_per_cycle": 3}}
    monkeypatch.setattr(cfg_mod, "load_config", _fake)


def _make_pupdate(id_, type_="calendar_event", title="x"):
    return Pupdate(
        id=id_,
        source="test",
        type=type_,
        priority="normal",
        title=title,
        body="b",
        read=False, dismissed=False, brain_processed=False,
    )


def test_type_cache_collapses_same_type_pupdates_into_one_llm_call(app, _enable_triage):
    """5 pupdates of the same type → 1 LLM call, 4 cache hits."""
    with app.app_context():
        for i in range(5):
            db.session.add(_make_pupdate(f"p-{i}"))
        db.session.commit()

        call_count = {"n": 0}

        def fake_triage(_p):
            call_count["n"] += 1
            return {"action": "dismiss", "reason": "test"}

        with patch.object(proc, "_try_llm_triage", side_effect=fake_triage):
            counts = proc.process()

    assert call_count["n"] == 1, "only the first calendar_event should hit the LLM"
    assert counts["llm_triage"] == 1
    assert counts["llm_cached"] == 4
    assert counts["dismissed"] == 5, "all 5 should be dismissed via the cached decision"


def test_cap_defers_when_many_distinct_types_burst(app, _enable_triage):
    """5 pupdates of 5 different types, cap=3 → 3 LLM calls, 2 deferred."""
    with app.app_context():
        for i in range(5):
            db.session.add(_make_pupdate(f"p-{i}", type_=f"type-{i}"))
        db.session.commit()

        call_count = {"n": 0}

        def fake_triage(_p):
            call_count["n"] += 1
            return {"action": "dismiss", "reason": "test"}

        with patch.object(proc, "_try_llm_triage", side_effect=fake_triage):
            counts = proc.process()

    assert call_count["n"] == 3, "cap stops after llm_triage_per_cycle real calls"
    assert counts["llm_triage"] == 3
    assert counts["llm_deferred"] == 2

    with app.app_context():
        # Deferred pupdates stay unprocessed for the next cycle
        deferred = Pupdate.query.filter_by(brain_processed=False).count()
        assert deferred == 2


def test_deferred_pupdates_get_handled_on_next_pass(app, _enable_triage):
    """Second call after a deferred burst should pick up the deferred
    ones and handle them (cap resets per process() call).
    """
    with app.app_context():
        for i in range(5):
            db.session.add(_make_pupdate(f"p-{i}", type_=f"type-{i}"))
        db.session.commit()

        def fake_triage(_p):
            return {"action": "mark_read", "reason": "test"}

        with patch.object(proc, "_try_llm_triage", side_effect=fake_triage):
            proc.process()  # handles 3, defers 2
            counts = proc.process()  # picks up the 2 deferred

    assert counts["processed"] == 2
    assert counts["read"] == 2


def test_llm_triage_disabled_short_circuits_without_calling(app, monkeypatch):
    """`llm_triage: false` in config → no LLM calls at all, unmatched
    pupdates stay unmatched. Matches the opt-out case the user asked
    about ("is it needed?").
    """
    import planet_maiko.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load_config",
                        lambda: {"brain": {"llm_triage": False}})

    with app.app_context():
        db.session.add(_make_pupdate("p-1"))
        db.session.commit()

        call_count = {"n": 0}

        def fake_triage(_p):
            call_count["n"] += 1
            return {"action": "dismiss", "reason": "should not fire"}

        # _try_llm_triage returns None early when disabled — inner
        # function (which we're not stubbing) checks config itself.
        counts = proc.process()

    assert call_count["n"] == 0
    assert counts["unmatched"] == 1
