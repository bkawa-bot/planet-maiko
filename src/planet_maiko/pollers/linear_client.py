"""Shared Linear GraphQL client.

Centralizes auth, error handling, and query dispatch. The poller keeps
its hand-rolled queries for polling-specific concerns; callers that
need one-off team metadata (the Send-to-Linear modal, the active-cycle
chip in Settings) go through this client so we have one authoritative
place for auth + headers + error shape.

Intentionally not a singleton — construct with an explicit api_key
override in tests; default constructor pulls from config.linear.api_key.
"""

import logging
import requests

from planet_maiko.config import load_config

logger = logging.getLogger(__name__)

LINEAR_API = "https://api.linear.app/graphql"


class LinearClient:
    """Thin GraphQL wrapper over the Linear API."""

    def __init__(self, api_key=None):
        self.api_key = api_key or self._load_api_key()
        if not self.api_key:
            raise ValueError("Linear API key not configured")

    @staticmethod
    def _load_api_key():
        return (load_config().get("linear") or {}).get("api_key") or None

    def query(self, query, variables=None, timeout=30):
        """Execute a GraphQL query or mutation.

        Raises RuntimeError if Linear returns errors in the response
        body (a 200 with a non-empty `errors` array — common for
        validation failures). HTTP errors bubble up via raise_for_status.
        """
        import certifi

        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = requests.post(
            LINEAR_API,
            json=payload,
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            },
            timeout=timeout,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Linear API errors: {data['errors']}")
        return data.get("data") or {}

    def fetch_teams(self):
        """Teams the API-key owner is a member of. Capped at 250 — larger
        workspaces would need pagination, but the picker UI is fine
        with any single-page result."""
        q = """
        query {
          viewer {
            teams(first: 250) {
              nodes { id name key }
            }
          }
        }
        """
        data = self.query(q)
        return (
            (data.get("viewer") or {})
            .get("teams", {})
            .get("nodes", [])
        ) or []

    def team_meta(self, team_id):
        """Fetch everything the Send-to-Linear modal needs in one hop.

        Returns a dict:
            {
                id, name, key,
                states: [{id, name, type, color, position}] sorted by position,
                labels: [{id, name, color, isGroup, parentId}],
                activeCycle: {id, number, name, startsAt, endsAt, progress} | None,
                upcomingCycles: [{id, number, name, startsAt, endsAt}, ...],
                projects: [{id, name, state}] — active + backlog + started only,
                members: [{id, name, displayName, email}],
                defaultAssigneeId: the viewer's userId,
            }

        One big query is cheaper than six round trips. Linear's
        complexity budget (10k per query) easily covers this shape.
        """
        q = """
        query($teamId: String!) {
          viewer { id name displayName }
          team(id: $teamId) {
            id name key
            states { nodes { id name type color position } }
            labels(first: 250) {
              nodes { id name color isGroup parent { id } }
            }
            activeCycle {
              id number name startsAt endsAt progress
            }
            cycles(first: 20, filter: { isFuture: { eq: true } }) {
              nodes { id number name startsAt endsAt }
            }
            projects(first: 100) { nodes { id name state } }
            members(first: 250) {
              nodes { id name displayName email }
            }
          }
        }
        """
        data = self.query(q, {"teamId": team_id})
        team = data.get("team") or {}
        viewer = data.get("viewer") or {}

        # Sort states by position so the UI shows them in workflow order
        # (backlog → unstarted → started → completed → canceled), which
        # is how Linear renders them natively.
        states_sorted = sorted(
            (team.get("states") or {}).get("nodes") or [],
            key=lambda s: (s.get("position") or 0),
        )

        labels_flat = []
        for l in (team.get("labels") or {}).get("nodes") or []:
            parent = l.get("parent") or {}
            labels_flat.append({
                "id": l.get("id"),
                "name": l.get("name"),
                "color": l.get("color"),
                "isGroup": bool(l.get("isGroup")),
                "parentId": parent.get("id"),
            })

        # Filter out canceled projects — the modal doesn't need those,
        # and they'd just clutter the picker.
        projects_filtered = [
            p for p in (team.get("projects") or {}).get("nodes") or []
            if p.get("state") not in ("canceled",)
        ]

        return {
            "id": team.get("id"),
            "name": team.get("name"),
            "key": team.get("key"),
            "states": states_sorted,
            "labels": labels_flat,
            "activeCycle": team.get("activeCycle"),
            "upcomingCycles": (team.get("cycles") or {}).get("nodes") or [],
            "projects": projects_filtered,
            "members": (team.get("members") or {}).get("nodes") or [],
            "defaultAssigneeId": viewer.get("id"),
        }

    def create_issue(self, team_id, title, **fields):
        """Create an issue. title + team_id are required. Any of the
        optional fields (description, stateId, priority, estimate,
        assigneeId, labelIds, cycleId, projectId, parentId, dueDate)
        land in IssueCreateInput if provided (non-None).

        Returns the created issue dict with at least id/identifier/url.
        """
        input_dict = {"teamId": team_id, "title": title}
        passthrough_fields = (
            "description", "stateId", "priority", "estimate",
            "assigneeId", "labelIds", "cycleId", "projectId",
            "parentId", "dueDate",
        )
        for key in passthrough_fields:
            value = fields.get(key)
            if value is not None:
                input_dict[key] = value

        mutation = """
        mutation IssueCreate($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue {
              id identifier url title
              state { id name type }
            }
          }
        }
        """
        data = self.query(mutation, {"input": input_dict})
        payload = data.get("issueCreate") or {}
        if not payload.get("success"):
            raise RuntimeError("Linear issueCreate returned success=false")
        return payload.get("issue") or {}

    def update_issue(self, issue_id, **fields):
        """Update an existing issue. `issue_id` accepts UUID or the
        identifier form ("ENG-42"). Fields set to None are skipped.

        Note: `labelIds` REPLACES the whole set. Use
        `addedLabelIds` / `removedLabelIds` for additive edits.
        """
        input_dict = {}
        for key, value in fields.items():
            if value is not None:
                input_dict[key] = value
        if not input_dict:
            return None  # nothing to update

        mutation = """
        mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue {
              id identifier url
              state { id name type }
            }
          }
        }
        """
        data = self.query(mutation, {"id": issue_id, "input": input_dict})
        payload = data.get("issueUpdate") or {}
        if not payload.get("success"):
            raise RuntimeError("Linear issueUpdate returned success=false")
        return payload.get("issue") or {}
