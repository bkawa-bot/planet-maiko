"""API endpoint smoke tests — hit key routes via the Flask test client."""

import json
import pytest


# ---------------------------------------------------------------------------
# Scene API
# ---------------------------------------------------------------------------


def test_get_scene_returns_200(client):
    resp = client.get("/api/scene")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "scene" in data
    assert "context" in data


def test_get_scene_accepts_weather_param(client):
    resp = client.get("/api/scene?weather=rain&temperature_f=50")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["context"]["weather"] == "rain"


# ---------------------------------------------------------------------------
# Pupdates API
# ---------------------------------------------------------------------------


def test_create_pupdate(client):
    payload = {
        "id": "test-pup-1",
        "source": "test",
        "type": "info",
        "title": "Test pupdate",
        "body": "This is a test",
        "priority": "normal",
    }
    resp = client.post("/api/pupdates", json=payload)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["id"] == "test-pup-1"
    assert data["title"] == "Test pupdate"


def test_list_pupdates_returns_list(client):
    # Create one first
    client.post("/api/pupdates", json={
        "id": "test-pup-list",
        "source": "test",
        "type": "info",
        "title": "Listable",
    })

    resp = client.get("/api/pupdates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_list_pupdates_filters_by_source(client):
    client.post("/api/pupdates", json={
        "id": "pup-gh", "source": "github", "type": "pr", "title": "GH Pupdate",
    })
    client.post("/api/pupdates", json={
        "id": "pup-lin", "source": "linear", "type": "issue", "title": "Linear Pupdate",
    })

    resp = client.get("/api/pupdates?source=github")
    data = resp.get_json()
    assert all(p["source"] == "github" for p in data)


# ---------------------------------------------------------------------------
# Tasks API
# ---------------------------------------------------------------------------


def test_create_task(client):
    payload = {
        "id": "test-task-1",
        "title": "Fix the flaky test",
        "type": "bug",
        "priority": "high",
    }
    resp = client.post("/api/tasks", json=payload)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["id"] == "test-task-1"
    assert data["title"] == "Fix the flaky test"
    assert data["status"] == "new"


def test_list_tasks(client):
    client.post("/api/tasks", json={
        "id": "task-list-1", "title": "Task 1",
    })
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1


# ---------------------------------------------------------------------------
# Profiles API
# ---------------------------------------------------------------------------


def test_create_profile_via_api(client):
    payload = {"agent_id": "agent-api-1", "display_name": "TestAgent"}
    resp = client.post("/api/profiles", json=payload)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["id"] == "agent-api-1"
    assert "TestAgent" in data["display_name"]


def test_create_profile_via_api_no_body(client):
    resp = client.post("/api/profiles", json={})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["id"] is not None
    assert data["display_name"] is not None


def test_get_profiles_with_role_filter(client):
    # The /profiles endpoint supports role + repo filters for the
    # assign-agent modal; /profiles/recommend (the old ranking
    # endpoint) was dropped along with rank/success_rate/breed.
    client.post("/api/profiles", json={"agent_id": "agent-rec-1", "role": "coding"})
    client.post("/api/profiles", json={"agent_id": "agent-rec-2", "role": "review"})

    resp = client.get("/api/profiles?role=review")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert all(p["role"] == "review" for p in data)


# ---------------------------------------------------------------------------
# Pack Insights API
# ---------------------------------------------------------------------------


def test_pack_insights_start(client):
    resp = client.post("/api/pack-insights/start")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "gathering"


def test_pack_insights_get_state(client):
    resp = client.get("/api/pack-insights")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "status" in data


# ---------------------------------------------------------------------------
# Diff review API — comment CRUD + request-changes composition
# ---------------------------------------------------------------------------


def _make_task(client, task_id="task-diff-1", title="Fix the widget"):
    resp = client.post("/api/tasks", json={
        "id": task_id,
        "title": title,
        "type": "todo",
    })
    assert resp.status_code == 201
    return resp.get_json()


def test_diff_comment_create_and_list(client):
    _make_task(client, "task-diff-list")
    resp = client.post("/api/tasks/task-diff-list/comments", json={
        "file_path": "src/widget.py",
        "line_number": 42,
        "side": "new",
        "body": "Should this handle the None case?",
    })
    assert resp.status_code == 201
    c = resp.get_json()
    assert c["author"] == "user"
    assert c["status"] == "draft"
    assert c["line_number"] == 42

    listing = client.get("/api/tasks/task-diff-list/comments")
    assert listing.status_code == 200
    body = listing.get_json()
    assert len(body) == 1
    assert body[0]["body"] == "Should this handle the None case?"


def test_diff_comment_delete_only_drafts(client):
    _make_task(client, "task-diff-del")
    created = client.post("/api/tasks/task-diff-del/comments", json={
        "file_path": "a.py", "line_number": 1, "body": "draft",
    }).get_json()
    # Submitted comments can't be deleted
    client.patch(f"/api/comments/{created['id']}", json={"status": "submitted"})
    resp = client.delete(f"/api/comments/{created['id']}")
    assert resp.status_code == 400

    # New draft can be deleted
    draft = client.post("/api/tasks/task-diff-del/comments", json={
        "file_path": "b.py", "line_number": 2, "body": "draft",
    }).get_json()
    resp = client.delete(f"/api/comments/{draft['id']}")
    assert resp.status_code == 200


def test_agent_authored_comment(client):
    _make_task(client, "task-diff-agent")
    resp = client.post("/api/tasks/task-diff-agent/comments/agent", json={
        "file_path": "src/thing.py",
        "line_number": 7,
        "body": "This branch is load-bearing — please double-check the ordering.",
    })
    assert resp.status_code == 201
    c = resp.get_json()
    assert c["author"] == "agent"
    assert c["status"] == "submitted"


def test_request_changes_requires_drafts(client):
    _make_task(client, "task-diff-req")
    # No drafts yet
    resp = client.post("/api/tasks/task-diff-req/review/request-changes")
    assert resp.status_code == 400
