"""Unit tests for the pollers' to_pupdates transforms.

Exercise the pieces of the pipeline that don't need the network:
given a realistic raw response, does each poller emit the expected
pupdates with the right source_ids, flags, and metadata?

These are exactly the bugs that bit us mid-session (Linear dedup
resurrecting dismissed pupdates, GitHub swallowing all review
requests via team-level tags). Locking them in with tests so
neither regresses.
"""

import pytest

from planet_maiko.database import db
from planet_maiko.models.task import Task
from planet_maiko.pollers.github_poller import GitHubPoller
from planet_maiko.pollers.linear_poller import LinearPoller


# ===========================================================================
# Linear
# ===========================================================================


def _linear_issue(identifier="PROJ-1", linear_id="uuid-1", title="Do thing",
                  state_type="started", state_name="In Progress",
                  priority=3, labels=None, due_date=None):
    return {
        "id": linear_id,
        "identifier": identifier,
        "title": title,
        "description": "body",
        "url": f"https://linear.app/foo/{identifier}",
        "priority": priority,
        "dueDate": due_date,
        "state": {"type": state_type, "name": state_name},
        "labels": {"nodes": [{"name": l} for l in (labels or [])]},
        "project": None,
        "updatedAt": "2026-04-15T10:00:00.000Z",
        "createdAt": "2026-04-01T10:00:00.000Z",
    }


def test_linear_to_pupdates_basic_emits_assigned_pupdate(app):
    poller = LinearPoller()
    raw = {"issues": [_linear_issue(identifier="PROJ-1", linear_id="u1")]}
    with app.app_context():
        out = poller.to_pupdates(raw)
    assert len(out) == 1
    p = out[0]
    assert p["type"] == "linear_assigned"
    assert p["source_id"] == "PROJ-1/assigned"
    assert p["actionable"] is True
    assert p["metadata"]["linear_id"] == "u1"
    assert p["metadata"]["identifier"] == "PROJ-1"


def test_linear_to_pupdates_skips_issues_already_tracked_by_task(app):
    """The ritual we fixed: if a Task already references this Linear
    issue via extra.linear_id, don't re-emit a pupdate. Otherwise
    base.py's resurrect path would reanimate dismissed pupdates on
    every poll.
    """
    poller = LinearPoller()
    raw = {
        "issues": [
            _linear_issue(identifier="TRACKED-1", linear_id="already-have"),
            _linear_issue(identifier="NEW-2", linear_id="fresh"),
        ]
    }
    with app.app_context():
        # User already has a Maiko task for TRACKED-1
        db.session.add(Task(
            id="task-tracked-1", title="TRACKED-1", status="in_progress",
            extra={"linear_id": "already-have", "identifier": "TRACKED-1"},
        ))
        db.session.commit()

        out = poller.to_pupdates(raw)

    identifiers = [p["metadata"]["identifier"] for p in out]
    assert identifiers == ["NEW-2"], "TRACKED-1 should be skipped, NEW-2 should emit"


def test_linear_to_pupdates_skips_by_identifier_even_without_linear_id(app):
    """Tasks created by old import flows may have identifier but no
    linear_id. We still skip — identifier is enough to dedupe.
    """
    poller = LinearPoller()
    raw = {"issues": [_linear_issue(identifier="OLD-1", linear_id="new-uuid")]}
    with app.app_context():
        db.session.add(Task(
            id="task-old-1", title="Old task", status="new",
            extra={"identifier": "OLD-1"},  # no linear_id
        ))
        db.session.commit()
        out = poller.to_pupdates(raw)
    assert out == []


# ===========================================================================
# GitHub review-request pipeline
# ===========================================================================


def test_github_to_pupdates_review_request_embeds_headrefoid_in_source_id():
    """Our recent fix re-adds headRefOid via per-PR enrichment so a
    re-request on a new commit isn't dedup-swallowed. Assert the SHA
    lands in source_id when present.
    """
    poller = GitHubPoller()
    raw = {
        "review_requests": [{
            "number": 42,
            "title": "Fix thing",
            "url": "https://github.com/org/repo/pull/42",
            "repository": {"nameWithOwner": "org/repo"},
            "author": {"login": "alice"},
            "labels": [],
            "headRefOid": "abc1234567",
        }],
        "my_prs": [],
        "merged_prs": [],
    }
    out = poller.to_pupdates(raw)
    review_events = [p for p in out if p["type"] == "pr_review_requested"]
    assert len(review_events) == 1
    assert review_events[0]["source_id"] == "review/org/repo#42@abc1234567"


def test_github_to_pupdates_review_request_falls_back_when_sha_missing():
    """Older gh installs won't have headRefOid. Source_id falls back
    to the SHA-less form so dedupe still works (though re-requests
    on new commits won't re-fire — acceptable regression).
    """
    poller = GitHubPoller()
    raw = {
        "review_requests": [{
            "number": 42,
            "title": "No SHA here",
            "url": "https://github.com/org/repo/pull/42",
            "repository": {"nameWithOwner": "org/repo"},
            "author": {"login": "alice"},
            "labels": [],
            # no headRefOid
        }],
        "my_prs": [],
        "merged_prs": [],
    }
    out = poller.to_pupdates(raw)
    review_events = [p for p in out if p["type"] == "pr_review_requested"]
    assert len(review_events) == 1
    assert review_events[0]["source_id"] == "review/org/repo#42"
