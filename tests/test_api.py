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


def test_get_recommend_returns_list(client):
    # Create a profile first
    client.post("/api/profiles", json={"agent_id": "agent-rec-1"})

    resp = client.get("/api/profiles/recommend")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_get_recommend_with_repo_param(client):
    client.post("/api/profiles", json={"agent_id": "agent-rec-2"})
    resp = client.get("/api/profiles/recommend?repo=my-repo&categories=testing,style")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_post_feedback(client):
    # Create profile + context selection so feedback has something to update
    client.post("/api/profiles", json={"agent_id": "agent-fb-api"})

    payload = {
        "task_id": "task-fb-api",
        "category": "style",
        "severity": "suggestion",
    }
    resp = client.post("/api/profiles/feedback", json=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert "recorded" in data


def test_post_feedback_missing_fields(client):
    resp = client.post("/api/profiles/feedback", json={"task_id": "x"})
    assert resp.status_code == 400


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
