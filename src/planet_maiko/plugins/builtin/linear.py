"""Linear plugin. Fetches assigned issues via the Linear GraphQL API.

Generates pupdates for:
    - Issues assigned to you
    - Issues approaching their due date
    - Issue status changes (synced back onto linked Maiko tasks)
    - @-mentions in issue bodies and comments
    - New comments on issues you're subscribed to

Also exposes static helpers used by api/tasks.py for the
manual create-issue / import flow.
"""

import logging
import re

import requests

from planet_maiko.config import load_config, save_config
from planet_maiko.plugins.poller import PollerPlugin

logger = logging.getLogger(__name__)

LINEAR_API = "https://api.linear.app/graphql"

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _looks_like_uuid(value):
    return bool(value and isinstance(value, str) and _UUID_RE.match(value.lower()))


# viewer.teams (not workspace-wide `teams`) returns teams the API-key
# owner is a member of. More relevant for a single-user integration,
# and avoids the unordered-pagination trap where the root `teams`
# connection returns a 50-wide slice missing the user's own team.
# first: 250 is Linear's per-page max.
TEAMS_QUERY = """
query {
  viewer {
    teams(first: 250) {
      nodes { id name key }
    }
  }
}
"""

# Maiko priority -> Linear priority. Linear: 0=No priority, 1=Urgent,
# 2=High, 3=Normal/Medium, 4=Low. "Normal" maps to 3 so new issues land
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

# `content` is the full markdown body the user edits in Linear's
# description editor; `description` is the one-liner shown under the
# title. We need `content` for /generate-plan to plan against.
LED_PROJECTS_QUERY = """
query {
  projects(filter: { lead: { isMe: { eq: true } } }, first: 50) {
    nodes {
      id
      name
      description
      content
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
          content
          url
          state
        }
        cycle {
          id
          number
          name
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


# Notification types we surface as their own pupdates. Issue assignment
# is already covered by assignedIssues; status changes flow via
# sync_statuses. Here we only care about the "someone said something to
# or about you" types.
NOTIFICATION_KINDS_OF_INTEREST = {
    "issueMention",
    "issueCommentMention",
    "issueNewComment",
}


# Pull recent notifications. We fetch the most recent 50 and filter
# unread + types in Python — slightly more bytes over the wire but
# immune to Linear's filter/orderBy syntax variations.
NOTIFICATIONS_QUERY = """
query Notifications {
  notifications(first: 50) {
    nodes {
      id
      type
      createdAt
      readAt
      ... on IssueNotification {
        actor {
          name
          displayName
        }
        botActor {
          name
        }
        externalUserActor {
          name
          displayName
        }
        issue {
          id
          identifier
          title
          url
        }
        comment {
          id
          body
          url
        }
      }
    }
  }
}
"""

NOTIFICATION_MARK_READ_MUTATION = """
mutation MarkNotificationRead($id: String!, $readAt: DateTime!) {
  notificationUpdate(id: $id, input: { readAt: $readAt }) {
    success
  }
}
"""


class LinearPlugin(PollerPlugin):
    name = "linear"

    def get_config_defaults(self):
        return {"linear": {"enabled": False, "poll_interval_minutes": 5, "api_key": "", "team_id": ""}}

    def get_setup_actions(self):
        return [{
            "key": "import",
            "label": "Import from Linear",
            "description": "Pull your assigned issues in as tasks, plus any projects you lead.",
        }]

    def run_setup_action(self, key):
        if key != "import":
            return super().run_setup_action(key)
        from planet_maiko.config import load_config
        api_key = (load_config().get("linear") or {}).get("api_key")
        if not api_key:
            raise ValueError("Linear API key not configured. Set it in Settings.")
        stats = LinearPlugin.import_issues(api_key)
        try:
            led = self.import_led_projects(api_key)
            stats["projects_created"] = stats.get("projects_created", 0) + led.get("created", 0)
            stats["projects_updated"] = stats.get("projects_updated", 0) + led.get("updated", 0)
        except Exception as e:
            stats["led_projects_note"] = f"led-project import failed: {e}"
        return stats

    def _query(self, api_key, query, variables=None):
        """Execute a GraphQL query/mutation against the Linear API."""
        import certifi
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = requests.post(
            LINEAR_API,
            json=payload,
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

        try:
            self.sync_statuses(issues)
        except Exception as e:
            logger.warning(f"[linear] Status sync failed: {e}")

        try:
            self.import_led_projects(api_key)
        except Exception as e:
            logger.warning(f"[linear] Led-project sync failed: {e}")

        notifications = []
        try:
            ndata = self._query(api_key, NOTIFICATIONS_QUERY)
            raw = ndata.get("notifications", {}).get("nodes", []) or []
            for n in raw:
                if n.get("readAt"):
                    continue
                if n.get("type") not in NOTIFICATION_KINDS_OF_INTEREST:
                    continue
                if not n.get("issue"):
                    continue
                notifications.append(n)
        except Exception as e:
            logger.warning(f"[linear] Notification fetch failed: {e}")

        return {"issues": issues, "notifications": notifications, "_api_key": api_key}

    @staticmethod
    def sync_statuses(issues):
        """Mirror Linear issue state onto Maiko tasks linked by extra.linear_id.

        Only touches tasks currently in new/in_progress; done/cancelled tasks
        are left alone. Returns {"updated": N}.
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
            cycle = issue.get("cycle") or {}
            cycle_id = cycle.get("id")
            cycle_changed = cycle_id != extra.get("linear_cycle_id")

            if (not new_status or new_status == t.status) and not cycle_changed:
                continue

            new_extra = dict(extra)
            if new_status and new_status != t.status:
                logger.info(f"[linear] Task {t.id} status {t.status} -> {new_status}")
                t.status = new_status
            if state.get("name"):
                new_extra["state"] = state["name"]
            if cycle_changed:
                if cycle_id:
                    new_extra["linear_cycle_id"] = cycle_id
                    new_extra["linear_cycle_number"] = cycle.get("number")
                    new_extra["linear_cycle_name"] = cycle.get("name")
                else:
                    new_extra.pop("linear_cycle_id", None)
                    new_extra.pop("linear_cycle_number", None)
                    new_extra.pop("linear_cycle_name", None)
            t.extra = new_extra
            updated += 1

        if updated:
            db.session.commit()
        return {"updated": updated}

    @staticmethod
    def _upsert_project_from_linear(lp, role="member"):
        """Create or update a Maiko Project from a Linear project node.

        Idempotent. Description is set on first import only; once a user
        (or /generate-plan) has edited it we never overwrite. Title, status,
        source_url, linear_state, and date hints always refresh.

        role: "lead" if the user leads the Linear project, "member" if they
        have an assigned issue under it.
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

        # `content` holds the full markdown body; `description` is the
        # short summary. Prefer content so /generate-plan has real material.
        linear_body = (lp.get("content") or "").strip()
        linear_summary = (lp.get("description") or "").strip()
        linear_desc = linear_body or linear_summary or None

        existing = db.session.get(Project, project_id)
        if existing:
            existing.title = name
            existing.status = new_status
            existing.source_url = lp.get("url") or existing.source_url
            if linear_desc and not (existing.description or "").strip():
                existing.description = linear_desc
            ex_extra = dict(existing.extra or {})
            # Promote to "lead" if the user is now lead; never demote.
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
            description=linear_desc,
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
        """Upsert Maiko projects for Linear projects the user leads."""
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
        # Skip issues already tracked by a Maiko task. Without this, every
        # poll re-asserts "you have PROJ-123 assigned" as an actionable
        # pupdate; user dismisses it; but since the task wasn't created
        # from *this specific pupdate*, the dismissal-resurrection lookup
        # via source_pupdate_id misses it and resurrection fires on the
        # next poll.
        from planet_maiko.models.task import Task

        tracked_linear_ids = set()
        tracked_identifiers = set()
        for t in Task.query.with_entities(Task.extra).all():
            ext = t.extra or {}
            lid = ext.get("linear_id")
            if lid:
                tracked_linear_ids.add(lid)
            ident = ext.get("identifier") or ext.get("linear_identifier")
            if ident:
                tracked_identifiers.add(ident)

        pupdates = []

        for issue in raw_data.get("issues", []):
            identifier = issue.get("identifier", "")
            linear_id = issue.get("id")
            if (linear_id and linear_id in tracked_linear_ids) or (
                identifier and identifier in tracked_identifiers
            ):
                continue

            title = issue.get("title", "")
            state = issue.get("state", {})
            state_name = state.get("name", "")
            priority = PRIORITY_MAP.get(issue.get("priority", 0), "normal")
            labels = [l["name"] for l in issue.get("labels", {}).get("nodes", [])]
            due_date = issue.get("dueDate")

            cycle = issue.get("cycle") or {}
            metadata = {
                "linear_id": linear_id,
                "identifier": identifier,
                "state": state_name,
                "due_date": due_date,
                # Carry the description through so the body survives the
                # pupdate -> task conversion. TaskCard reads
                # t.extra.description for the expanded view.
                "description": issue.get("description") or "",
            }
            if cycle:
                metadata["linear_cycle_id"] = cycle.get("id")
                metadata["linear_cycle_number"] = cycle.get("number")
                metadata["linear_cycle_name"] = cycle.get("name")

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
                "metadata": metadata,
            })

            # Due-date warning (if due within 2 days)
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

        # Notifications: @-mentions + new comments. source_id is the
        # Linear notification id for precise dedup. _after_sync marks
        # them read on Linear so the bell icon doesn't pile up.
        for n in raw_data.get("notifications", []):
            issue = n.get("issue") or {}
            comment = n.get("comment") or {}
            ntype = n.get("type") or ""
            identifier = issue.get("identifier", "")
            issue_title = issue.get("title", "")
            # Actor resolution: Linear puts the trigger on one of three
            # fields depending on who/what fired the notification.
            # Without checking all three, a bot-triggered or external-user
            # notification falls through to a generic "someone" placeholder.
            actor = n.get("actor") or n.get("externalUserActor") or n.get("botActor") or {}
            actor_name = (
                actor.get("displayName")
                or actor.get("name")
                or "Someone"
            )

            is_mention = ntype in ("issueMention", "issueCommentMention")
            priority = "high" if is_mention else "normal"

            if ntype == "issueMention":
                kind_label = "mentioned you in"
                pupdate_type = "linear_mention"
                action_hint = "Open in Linear"
                body = f"{actor_name} mentioned you in {identifier}: {issue_title}"
            elif ntype == "issueCommentMention":
                kind_label = "mentioned you in a comment on"
                pupdate_type = "linear_mention"
                action_hint = "Open comment"
                body = (comment.get("body") or "")[:500] or f"Comment by {actor_name} on {identifier}"
            else:  # issueNewComment
                kind_label = "commented on"
                pupdate_type = "linear_comment"
                action_hint = "Open comment"
                body = (comment.get("body") or "")[:500] or f"New comment on {identifier}"

            url = comment.get("url") or issue.get("url", "")
            if identifier and issue_title:
                title = f"{actor_name} {kind_label} {identifier}: {issue_title}"
            elif identifier:
                title = f"{actor_name} {kind_label} {identifier}"
            elif issue_title:
                title = f"{actor_name} {kind_label} {issue_title}"
            else:
                title = f"{actor_name} {kind_label} a Linear issue"

            pupdates.append({
                "source_id": f"notification/{n.get('id')}",
                "type": pupdate_type,
                "priority": priority,
                "title": title[:200],
                "body": body,
                "url": url,
                "actionable": True,
                "action_hint": action_hint,
                "tags": [identifier, "linear", "mention" if is_mention else "comment"],
                "metadata": {
                    "linear_notification_id": n.get("id"),
                    "linear_notification_type": ntype,
                    "issue_id": issue.get("id"),
                    "identifier": identifier,
                    "comment_id": comment.get("id"),
                    "actor_name": actor_name,
                    "created_at": n.get("createdAt"),
                },
            })

        return pupdates

    def _after_sync(self, raw_data, db_session):
        """Mark the notifications we just ingested as read on Linear."""
        from datetime import datetime as _dt, timezone as _tz
        api_key = raw_data.get("_api_key")
        notifications = raw_data.get("notifications") or []
        if not api_key or not notifications:
            return
        read_at = _dt.now(_tz.utc).isoformat()
        marked = 0
        for n in notifications:
            nid = n.get("id")
            if not nid:
                continue
            try:
                self._query(
                    api_key,
                    NOTIFICATION_MARK_READ_MUTATION,
                    variables={"id": nid, "readAt": read_at},
                )
                marked += 1
            except Exception as e:
                logger.debug(f"[linear] mark-read failed for notification {nid}: {e}")
        if marked:
            logger.info(f"[linear] Marked {marked} notification(s) read after ingest")

    @staticmethod
    def fetch_teams(api_key=None):
        """Fetch the user's Linear teams. Returns list of {id, name, key}."""
        import certifi

        config = load_config()
        api_key = api_key or config.get("linear", {}).get("api_key", "")
        if not api_key:
            raise ValueError("Linear API key not configured")

        resp = requests.post(
            LINEAR_API,
            json={"query": TEAMS_QUERY},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=15,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Linear API errors: {data['errors']}")
        return (
            (data.get("data") or {})
            .get("viewer", {})
            .get("teams", {})
            .get("nodes", [])
        ) or []

    @staticmethod
    def create_issue(task, description="", team_id=None, project_id=None, api_key=None):
        """Create a Linear issue from a Maiko task.

        Args:
            task: a Task ORM instance (has title, priority, due_date).
            description: markdown body for the Linear issue.
            team_id: Linear team UUID override. Falls back to config.linear.team_id.
            project_id: optional Linear project ID to assign the issue to.
            api_key: optional override. Falls back to config.linear.api_key.

        Returns:
            dict with {id, identifier, url, title}.
        """
        import certifi

        config = load_config()
        linear_cfg = config.get("linear", {})
        api_key = api_key or linear_cfg.get("api_key", "")
        if not api_key:
            raise ValueError("Linear API key not configured")
        team_id = team_id or linear_cfg.get("team_id", "")

        # Linear's issueCreate wants a UUID. If the config has a short
        # team key instead, fetch teams and auto-pick the only one.
        if not _looks_like_uuid(team_id):
            teams = LinearPlugin.fetch_teams(api_key=api_key)
            if len(teams) == 1:
                team_id = teams[0]["id"]
                linear_cfg["team_id"] = team_id
                config["linear"] = linear_cfg
                save_config(config)
            elif len(teams) > 1:
                raise ValueError(
                    "Pick your Linear team in Settings, we need the team UUID, not the key."
                )
            else:
                raise ValueError("No Linear teams found for this API key")

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

        Returns {projects_created, projects_updated, tasks_created, tasks_skipped}.
        """
        from planet_maiko.database import db
        from planet_maiko.models.task import Task

        poller = LinearPlugin()
        data = poller._query(api_key, ASSIGNED_ISSUES_QUERY)
        issues = data.get("viewer", {}).get("assignedIssues", {}).get("nodes", [])

        stats = {"projects_created": 0, "projects_updated": 0, "tasks_created": 0, "tasks_skipped": 0}

        # Pre-fetch task.extra blobs once so the "already imported?" check
        # is O(N + M) instead of O(N * M). Look up by linear_id/identifier
        # in extra to cover all three task-creation paths.
        existing_linear_ids = set()
        existing_identifiers = set()
        for t in Task.query.with_entities(Task.extra).all():
            ext = t.extra or {}
            lid = ext.get("linear_id")
            if lid:
                existing_linear_ids.add(lid)
            ident = ext.get("identifier") or ext.get("linear_identifier")
            if ident:
                existing_identifiers.add(ident)

        for issue in issues:
            identifier = issue.get("identifier", "")
            linear_id = issue.get("id")
            task_id = f"task-{identifier.lower()}"

            already_present = (
                db.session.get(Task, task_id) is not None
                or (linear_id and linear_id in existing_linear_ids)
                or (identifier and identifier in existing_identifiers)
            )
            if already_present:
                stats["tasks_skipped"] += 1
                continue

            project_id = None
            linear_project = issue.get("project")
            if linear_project and linear_project.get("id"):
                project_id, action = LinearPlugin._upsert_project_from_linear(
                    linear_project, role="member",
                )
                if action == "created":
                    stats["projects_created"] += 1
                elif action == "updated":
                    stats["projects_updated"] += 1

            state_type = issue.get("state", {}).get("type", "")
            status_map = {
                "backlog": "new", "unstarted": "new", "started": "in_progress",
                "completed": "done", "canceled": "cancelled",
            }
            status = status_map.get(state_type, "new")

            priority = PRIORITY_MAP.get(issue.get("priority", 0), "normal")
            labels = [l["name"] for l in issue.get("labels", {}).get("nodes", [])]

            extra = {
                "linear_id": issue.get("id"),
                "identifier": identifier,
                "due_date": issue.get("dueDate"),
                "state": issue.get("state", {}).get("name"),
                "description": issue.get("description") or "",
            }
            cycle = issue.get("cycle")
            if cycle:
                extra["linear_cycle_id"] = cycle.get("id")
                extra["linear_cycle_number"] = cycle.get("number")
                extra["linear_cycle_name"] = cycle.get("name")
            task = Task(
                id=task_id,
                title=f"{identifier}: {issue.get('title', '')}",
                type="todo",
                status=status,
                priority=priority,
                project_id=project_id,
                url=issue.get("url", ""),
                tags=[identifier] + labels,
                extra=extra,
            )
            db.session.add(task)
            stats["tasks_created"] += 1

        db.session.commit()
        return stats
