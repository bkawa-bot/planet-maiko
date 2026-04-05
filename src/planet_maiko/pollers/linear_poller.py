"""Linear poller - fetches assigned issues via the Linear GraphQL API.

Generates pupdates for:
    - Issues assigned to you
    - Issues approaching their due date
    - Issue status changes
"""

import logging
import requests

from planet_maiko.pollers.base import BasePoller

logger = logging.getLogger(__name__)

LINEAR_API = "https://api.linear.app/graphql"

ASSIGNED_ISSUES_QUERY = """
query {
  viewer {
    assignedIssues(
      filter: {
        state: { type: { nin: ["completed", "canceled"] } }
      }
      first: 50
      orderBy: updatedAt
    ) {
      nodes {
        id
        identifier
        title
        description
        url
        priority
        dueDate
        state {
          name
          type
        }
        labels {
          nodes {
            name
          }
        }
        project {
          id
          name
          url
          state
        }
        updatedAt
        createdAt
      }
    }
  }
}
"""

# Linear priority: 0=No priority, 1=Urgent, 2=High, 3=Normal, 4=Low
PRIORITY_MAP = {0: "normal", 1: "urgent", 2: "high", 3: "normal", 4: "low"}


class LinearPoller(BasePoller):

    @property
    def name(self):
        return "linear"

    def _query(self, api_key, query):
        """Execute a GraphQL query against the Linear API."""
        import certifi
        resp = requests.post(
            LINEAR_API,
            json={"query": query},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=30,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Linear API errors: {data['errors']}")
        return data["data"]

    def poll(self, config):
        api_key = config.get("api_key", "")
        if not api_key:
            logger.warning("[linear] No API key configured, skipping poll")
            return {"issues": []}

        data = self._query(api_key, ASSIGNED_ISSUES_QUERY)
        issues = data.get("viewer", {}).get("assignedIssues", {}).get("nodes", [])
        return {"issues": issues}

    def to_pupdates(self, raw_data):
        pupdates = []

        for issue in raw_data.get("issues", []):
            identifier = issue.get("identifier", "")
            title = issue.get("title", "")
            state = issue.get("state", {})
            state_name = state.get("name", "")
            priority = PRIORITY_MAP.get(issue.get("priority", 0), "normal")
            labels = [l["name"] for l in issue.get("labels", {}).get("nodes", [])]
            due_date = issue.get("dueDate")

            # Main assignment pupdate
            pupdates.append({
                "source_id": f"{identifier}/assigned",
                "type": "linear_assigned",
                "priority": priority,
                "title": f"{identifier}: {title}",
                "body": f"Status: {state_name}",
                "url": issue.get("url", ""),
                "actionable": True,
                "action_hint": "Create task",
                "tags": [identifier] + labels,
                "metadata": {
                    "linear_id": issue.get("id"),
                    "identifier": identifier,
                    "state": state_name,
                    "due_date": due_date,
                },
            })

            # Due date warning (if due within 2 days)
            if due_date:
                from datetime import datetime, timezone, timedelta
                try:
                    due = datetime.fromisoformat(due_date)
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    if timedelta(0) < (due - now) < timedelta(days=2):
                        pupdates.append({
                            "source_id": f"{identifier}/due-soon",
                            "type": "linear_due_soon",
                            "priority": "high",
                            "title": f"Due soon: {identifier}: {title}",
                            "body": f"Due {due_date}",
                            "url": issue.get("url", ""),
                            "actionable": True,
                            "action_hint": "Prioritize",
                            "tags": [identifier, "due-soon"] + labels,
                            "metadata": {
                                "identifier": identifier,
                                "due_date": due_date,
                            },
                        })
                except (ValueError, TypeError):
                    pass

        return pupdates

    @staticmethod
    def import_issues(api_key):
        """Import Linear issues as tasks, with project associations.

        Creates Maiko projects for Linear projects (if not existing),
        then creates tasks linked to those projects.

        Returns:
            dict with counts: {projects_created, tasks_created, tasks_skipped}
        """
        from planet_maiko.database import db
        from planet_maiko.models.task import Task
        from planet_maiko.models.project import Project

        poller = LinearPoller()
        data = poller._query(api_key, ASSIGNED_ISSUES_QUERY)
        issues = data.get("viewer", {}).get("assignedIssues", {}).get("nodes", [])

        stats = {"projects_created": 0, "tasks_created": 0, "tasks_skipped": 0}

        for issue in issues:
            identifier = issue.get("identifier", "")
            task_id = f"task-{identifier.lower()}"

            # Skip if task already exists
            if db.session.get(Task, task_id):
                stats["tasks_skipped"] += 1
                continue

            # Create project if issue has one
            project_id = None
            linear_project = issue.get("project")
            if linear_project and linear_project.get("id"):
                project_id = f"proj-linear-{linear_project['id'][:8]}"
                existing_project = db.session.get(Project, project_id)
                if not existing_project:
                    proj = Project(
                        id=project_id,
                        title=linear_project.get("name", "Untitled Project"),
                        status="active" if linear_project.get("state") == "started" else "planning",
                        source_type="linear",
                        source_id=linear_project.get("id"),
                        source_url=linear_project.get("url"),
                    )
                    db.session.add(proj)
                    stats["projects_created"] += 1

            # Map Linear state to Maiko status
            state_type = issue.get("state", {}).get("type", "")
            status_map = {
                "backlog": "new", "unstarted": "new", "started": "in_progress",
                "completed": "done", "canceled": "cancelled",
            }
            status = status_map.get(state_type, "new")

            # Create task
            priority = PRIORITY_MAP.get(issue.get("priority", 0), "normal")
            labels = [l["name"] for l in issue.get("labels", {}).get("nodes", [])]

            task = Task(
                id=task_id,
                title=f"{identifier}: {issue.get('title', '')}",
                type="todo",
                status=status,
                priority=priority,
                project_id=project_id,
                url=issue.get("url", ""),
                tags=[identifier] + labels,
                extra={
                    "linear_id": issue.get("id"),
                    "identifier": identifier,
                    "due_date": issue.get("dueDate"),
                    "state": issue.get("state", {}).get("name"),
                },
            )
            db.session.add(task)
            stats["tasks_created"] += 1

        db.session.commit()
        return stats
