"""Tests for the auto-investigation hook off the correlator."""

from unittest.mock import patch

import pytest

from planet_maiko.brain.pupdates.auto_investigate import maybe_auto_investigate
from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task


def _incident(service="org/repo"):
    return Pupdate(
        id="incident-1",
        source="maiko",
        type="incident",
        priority="high",
        title="Incident: api-service (ci_fail + error_spike)",
        body="Correlated events",
        tags=[service, "incident"],
        extra={
            "services": [service],
            "pattern": ["pr_ci_failed", "error_spike"],
            "correlated_ids": ["a", "b"],
        },
    )


def _set_config(monkeypatch, auto_cfg):
    import planet_maiko.config as cfg_mod

    def _fake_load():
        return {"brain": {"auto_investigate": auto_cfg}, "github": {"repo_roots": []}}
    monkeypatch.setattr(cfg_mod, "load_config", _fake_load)


def test_disabled_returns_none(app, monkeypatch):
    _set_config(monkeypatch, {"enabled": False})
    with app.app_context():
        db.session.add(_incident())
        db.session.commit()

        result = maybe_auto_investigate(Pupdate.query.first())
        assert result is None
        assert Task.query.count() == 0


def test_no_local_clone_returns_none(app, monkeypatch):
    """If the repo isn't in repo_roots, we can't create a worktree.

    The function returns None + logs a warning instead of creating a
    dangling task. Matches resolve_repo_path → None path.
    """
    _set_config(monkeypatch, {"enabled": True, "dry_run": True, "daily_budget": 5})

    # resolve_repo_path will return None (no repo_roots configured).
    with app.app_context():
        db.session.add(_incident())
        db.session.commit()

        result = maybe_auto_investigate(Pupdate.query.first())
        assert result is None
        assert Task.query.count() == 0


def test_dry_run_creates_task_but_no_agent_kickoff(app, monkeypatch, tmp_path):
    """dry_run=True: task exists with auto_spawned=True, no prepare call."""
    _set_config(monkeypatch, {"enabled": True, "dry_run": True, "daily_budget": 5})

    # Stub resolve_repo_path to return a valid path so the "can't find
    # local clone" early-exit doesn't fire. Use the tmp_path so we
    # don't accidentally touch a real repo.
    import planet_maiko.orchestration as orch

    fake_repo = str(tmp_path / "fake-repo")
    (tmp_path / "fake-repo").mkdir()
    monkeypatch.setattr(orch, "resolve_repo_path", lambda repo: fake_repo)

    # prepare / _kickoff_agent_headless should NOT be called in dry_run.
    prepare_called = {"n": 0}
    kickoff_called = {"n": 0}

    import planet_maiko.agents.coding_agent as ca
    monkeypatch.setattr(ca, "prepare", lambda **kw: prepare_called.__setitem__("n", prepare_called["n"] + 1) or {"working_path": "/unused"})
    monkeypatch.setattr(ca, "_kickoff_agent_headless", lambda *a, **k: kickoff_called.__setitem__("n", kickoff_called["n"] + 1) or {})

    with app.app_context():
        db.session.add(_incident())
        db.session.commit()

        task = maybe_auto_investigate(Pupdate.query.first())

        assert task is not None
        assert task.type == "investigation"
        assert (task.extra or {}).get("auto_spawned") is True
        assert (task.extra or {}).get("services") == ["org/repo"]
        assert task.assigned_agent_id, "should have been assigned to an investigation agent"
        assert task.source_pupdate_id == "incident-1"

    # dry_run means prepare should not have been called
    assert prepare_called["n"] == 0
    assert kickoff_called["n"] == 0


def test_daily_budget_halts_after_n(app, monkeypatch, tmp_path):
    _set_config(monkeypatch, {"enabled": True, "dry_run": True, "daily_budget": 1})

    import planet_maiko.orchestration as orch
    fake_repo = str(tmp_path / "r")
    (tmp_path / "r").mkdir()
    monkeypatch.setattr(orch, "resolve_repo_path", lambda repo: fake_repo)

    with app.app_context():
        # Seed an existing auto_spawned investigation task today
        db.session.add(Task(
            id="t-existing",
            title="Earlier auto-investigation",
            type="investigation",
            status="done",
            extra={"auto_spawned": True},
        ))
        db.session.add(_incident())
        db.session.commit()

        result = maybe_auto_investigate(Pupdate.query.first())
        assert result is None, "budget exceeded — no new task"

        # Only the seed task exists
        assert Task.query.count() == 1
