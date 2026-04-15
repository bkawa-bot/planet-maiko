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

# Maiko priority → Linear priority. Linear uses 0=No priority, 1=Urgent,
# 2=High, 3=Normal/Medium, 4=Low. We pick 3 for "normal" so new issues land
# in the middle of the queue rather than on top.
MAIKO_TO_LINEAR_PRIORITY = {"urgent": 1, "high": 2, "normal": 3, "low": 4}

ISSUE_CREATE_MUTATION = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier url title }
  }
}
"""

LED_PROJECTS_QUERY = """
query {
  projects(filter: { lead: { isMe: { eq: true } } }, first: 50) {
    nodes {
      id
      name
      description
      url
      state
      targetDate
      startDate
      updatedAt
    }
  }
}
"""

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
          description
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

        # Push Linear state changes back onto any linked Maiko tasks.
        # Runs best-effort so a sync hiccup doesn't break the whole poll.
        try:
            self.sync_statuses(issues)
        except Exception as e:
            logger.warning(f"[linear] Status sync failed: {e}")

        # Pull projects the viewer leads (they won't necessarily show up
        # through assigned issues). Best-effort.
        try:
            self.import_led_projects(api_key)
        except Exception as e:
            logger.warning(f"[linear] Led-project sync failed: {e}")

        return {"issues": issues}

    @staticmethod
    def sync_statuses(issues):
        """Mirror Linear issue state onto Maiko tasks linked by extra.linear_id.

        Only touches tasks currently in new/in_progress; done/cancelled tasks
        are left alone on the assumption the user has moved on. Returns a
        dict with the number of tasks updated.
        """
        from planet_maiko.database import db
        from planet_maiko.models.task import Task

        if not issues:
            return {"updated": 0}

        state_type_to_status = {
            "backlog": "new", "unstarted": "new",
            "started": "in_progress",
            "completed": "done", "canceled": "cancelled",
        }

        issue_by_id = {i.get("id"): i for i in issues if i.get("id")}
        if not issue_by_id:
            return {"updated": 0}

        active = Task.query.filter(Task.status.in_(["new", "in_progress"])).all()
        updated = 0
        for t in active:
            extra = t.extra or {}
            lid = extra.get("linear_id")
            if not lid or lid not in issue_by_id:
                continue
            issue = issue_by_id[lid]
            state = issue.get("state") or {}
            new_status = state_type_to_status.get(state.get("type", ""))
            if not new_status or new_status == t.status:
                continue
            logger.info(f"[linear] Task {t.id} status {t.status} → {new_status}")
            t.status = new_status
            # Keep stored state name fresh for downstream display.
            new_extra = dict(extra)
            if state.get("name"):
                new_extra["state"] = state["name"]
            t.extra = new_extra
            updated += 1

        if updated:
            db.session.commit()
        return {"updated": updated}

    @staticmethod
    def _upsert_project_from_linear(lp, role="member"):
        """Create or update a Maiko Project from a Linear project node.

        Idempotent. Returns (project_id, action) where action is
        "created" | "updated" | "skipped". Description is set ONLY on
        first import — once a Maiko user (or /generate-plan) has
        edited it, we never overwrite. Title, status, source_url, and
        the linear_state / target_date / start_date hints in extra
        always refresh so Linear-side changes flow through.

        role: "lead" if the user leads the project on Linear,
              "member" if they have an assigned issue under it.
              Stored in extra.role for downstream display.
        """
        from planet_maiko.database import db
        from planet_maiko.models.project import Project

        linear_id = lp.get("id")
        if not linear_id:
            return None, "skipped"

        project_id = f"proj-linear-{linear_id[:8]}"
        name = lp.get("name") or "Untitled Project"
        linear_state = lp.get("state", "")
        state_to_status = {
            "started": "active",
            "completed": "done",
            "canceled": "cancelled",
            "paused": "paused",
        }
        new_status = state_to_status.get(linear_state, "planning")

        existing = db.session.get(Project, project_id)
        if existing:
            existing.title = name
            existing.status = new_status
            existing.source_url = lp.get("url") or existing.source_url
            ex_extra = dict(existing.extra or {})
            # Bump role to "lead" if the user is now the lead — never
            # demote (lead is a strictly stronger relationship than member).
            if role == "lead" or not ex_extra.get("role"):
                ex_extra["role"] = role
            ex_extra["linear_state"] = linear_state
            if lp.get("targetDate"):
                ex_extra["target_date"] = lp["targetDate"]
            if lp.get("startDate"):
                ex_extra["start_date"] = lp["startDate"]
            existing.extra = ex_extra
            return project_id, "updated"

        proj = Project(
            id=project_id,
            title=name,
            description=lp.get("description") or None,
            status=new_status,
            source_type="linear",
            source_id=linear_id,
            source_url=lp.get("url"),
            extra={
                "role": role,
                "linear_state": linear_state,
                "target_date": lp.get("targetDate"),
                "start_date": lp.get("startDate"),
            },
        )
        db.session.add(proj)
        return project_id, "created"

    def import_led_projects(self, api_key):
        """Create / update Maiko projects for Linear projects the user leads.

        Called on every Linear poll so new lead assignments show up without
        a manual import. Idempotent.
        """
        from planet_maiko.database import db

        data = self._query(api_key, LED_PROJECTS_QUERY)
        projects = (data.get("projects") or {}).get("nodes") or []

        created, updated = 0, 0
        for lp in projects:
            _, action = self._upsert_project_from_linear(lp, role="lead")
            if action == "created":
                created += 1
            elif action == "updated":
                updated += 1

        if created or updated:
            db.session.commit()
            logger.info(f"[linear] Led projects: {created} created, {updated} updated")
        return {"created": created, "updated": updated}

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
                "body": issue.get("description") or f"Status: {state_name}",
                "url": issue.get("url", ""),
                "actionable": True,
                "action_hint": "Create task",
                "tags": [identifier] + labels,
                "metadata": {
                    "linear_id": issue.get("id"),
                    "identifier": identifier,
                    "state": state_name,
                    "due_date": due_date,
                    # Carry the description through so when the pupdate
                    # is converted to a Task the body survives —
                    # TaskCard reads t.extra.description for the
                    # expanded view, otherwise tasks end up titled
                    # but blank.
                    "description": issue.get("description") or "",
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
    def create_issue(task, description="", team_id=None, project_id=None, api_key=None):
        """Create a Linear issue from a Maiko task.

        Args:
            task: a Task ORM instance (has title, priority, due_date).
            description: markdown body for the Linear issue. Typically
                sourced from the task's originating pupdate.
            team_id: Linear team ID override. Falls back to
                config.linear.team_id.
            project_id: optional Linear project ID to assign the issue to.
            api_key: optional API key override. Falls back to
                config.linear.api_key.

        Returns:
            dict with {id, identifier, url, title}.

        Raises:
            ValueError: if API key or team_id is missing.
            RuntimeError: if the Linear API returns errors.
        """
        import certifi

        config = load_config()
        linear_cfg = config.get("linear", {})
        api_key = api_key or linear_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("Linear API key not configured")
        team_id = team_id or linear_cfg.get("team_id", "")
        if not team_id:
            raise ValueError("Linear team_id not configured")

        input_data = {
            "teamId": team_id,
            "title": task.title or "(Untitled)",
            "priority": MAIKO_TO_LINEAR_PRIORITY.get(task.priority, 3),
        }
        if description:
            input_data["description"] = description[:8000]
        if task.due_date:
            input_data["dueDate"] = task.due_date
        if project_id:
            input_data["projectId"] = project_id

        resp = requests.post(
            LINEAR_API,
            json={"query": ISSUE_CREATE_MUTATION, "variables": {"input": input_data}},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=30,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Linear API errors: {data['errors']}")

        create_result = (data.get("data") or {}).get("issueCreate") or {}
        if not create_result.get("success"):
            raise RuntimeError("Linear issueCreate returned success=false")
        issue = create_result.get("issue") or {}
        return {
            "id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "url": issue.get("url"),
            "title": issue.get("title"),
        }

    @staticmethod
    def import_issues(api_key):
        """Import Linear issues as tasks, with project associations.

        Upserts Maiko projects for any Linear project the issues belong to
        (via the shared _upsert_project_from_linear helper, so existing
        projects get their title/status/dates refreshed instead of being
        skipped), then creates one task per issue linked to that project.

        Returns:
            dict with counts: {projects_created, projects_updated,
            tasks_created, tasks_skipped}
        """
        from planet_maiko.database import db
        from planet_maiko.models.task import Task

        poller = LinearPoller()
        data = poller._query(api_key, ASSIGNED_ISSUES_QUERY)
        issues = data.get("viewer", {}).get("assignedIssues", {}).get("nodes", [])

        stats = {"projects_created": 0, "projects_updated": 0, "tasks_created": 0, "tasks_skipped": 0}

        for issue in issues:
            identifier = issue.get("identifier", "")
            task_id = f"task-{identifier.lower()}"

            # Skip if task already exists
            if db.session.get(Task, task_id):
                stats["tasks_skipped"] += 1
                continue

            # Upsert the project via the shared helper so issue-path and
            # led-path share state mapping + description semantics.
            project_id = None
            linear_project = issue.get("project")
            if linear_project and linear_project.get("id"):
                project_id, action = LinearPoller._upsert_project_from_linear(
                    linear_project, role="member",
                )
                if action == "created":
                    stats["projects_created"] += 1
                elif action == "updated":
                    stats["projects_updated"] += 1

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
                    "description": issue.get("description") or "",
                },
            )
            db.session.add(task)
            stats["tasks_created"] += 1

        db.session.commit()
        return stats
