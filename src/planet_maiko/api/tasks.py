from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.task import Task

tasks_bp = Blueprint("tasks", __name__)


@tasks_bp.route("/tasks", methods=["GET"])
def list_tasks():
    """List tasks with optional filtering and pagination."""
    status = request.args.get("status")
    priority = request.args.get("priority")
    project_id = request.args.get("project_id")
    limit = min(int(request.args.get("limit", 200)), 500)
    offset = int(request.args.get("offset", 0))

    query = Task.query
    if status:
        query = query.filter_by(status=status)
    if priority:
        query = query.filter_by(priority=priority)
    if project_id:
        query = query.filter_by(project_id=project_id)

    tasks = query.order_by(Task.created_at.asc()).limit(limit).offset(offset).all()
    return jsonify([t.to_dict() for t in tasks])


@tasks_bp.route("/tasks/<task_id>", methods=["GET"])
def get_task(task_id):
    """Get a single task by ID."""
    task = db.get_or_404(Task, task_id)
    return jsonify(task.to_dict())


@tasks_bp.route("/tasks", methods=["POST"])
def create_task():
    """Create a new task. `id` is auto-generated if the client doesn't
    provide one — matches the pattern used by _act_create_task so the
    same task-{hex10} shape lands regardless of origin."""
    import uuid
    data = request.get_json()
    task_id = data.get("id") or f"task-{uuid.uuid4().hex[:10]}"
    task = Task(
        id=task_id,
        title=data["title"],
        type=data.get("type", "todo"),
        status=data.get("status", "new"),
        priority=data.get("priority", "normal"),
        source_pupdate_id=data.get("source_pupdate_id"),
        project_id=data.get("project_id"),
        url=data.get("url"),
        tags=data.get("tags", []),
        extra=data.get("metadata", {}),
        due_date=data.get("due_date"),
    )
    db.session.add(task)
    db.session.commit()

    from planet_maiko.plugins.loader import fire_hook
    fire_hook("on_task_created", task)

    return jsonify(task.to_dict()), 201


@tasks_bp.route("/tasks/<task_id>", methods=["PATCH"])
def update_task(task_id):
    """Update task fields (title, priority, tags, metadata)."""
    task = db.get_or_404(Task, task_id)
    data = request.get_json()

    for field in ["title", "type", "status", "priority", "url", "tags", "project_id", "assigned_agent_id", "due_date"]:
        if field in data:
            setattr(task, field, data[field])
    if "metadata" in data:
        task.extra = data["metadata"]

    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(task.to_dict())


@tasks_bp.route("/tasks/<task_id>/start", methods=["POST"])
def start_task(task_id):
    """Mark a task as in progress."""
    task = db.get_or_404(Task, task_id)
    if task.status in ("done", "cancelled"):
        return jsonify({"error": f"Cannot start a {task.status} task"}), 400
    task.status = "in_progress"
    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(task.to_dict())


@tasks_bp.route("/tasks/<task_id>/done", methods=["POST"])
def complete_task(task_id):
    """Mark a task as done — deletes it from the active list and
    removes any agent worktree backing it. The agent's last reply
    + its task.extra.artifact already capture the result; the
    worktree itself is just throwaway scratch space at this point."""
    from planet_maiko.agents.coding_agent import cleanup_task_worktree
    task = db.get_or_404(Task, task_id)
    cleanup_task_worktree(task)
    db.session.delete(task)
    db.session.commit()
    return jsonify({"status": "deleted", "id": task_id})


@tasks_bp.route("/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task(task_id):
    """Cancel a task — terminate any in-flight agent subprocess, remove
    the worktree, and delete the task row.

    Order matters: we stop the subprocess FIRST so it can't race-write
    new commits or MCP replies into a worktree we're about to delete.
    Worktree removal and task delete are idempotent; the subprocess
    stop is best-effort (no-op if nothing is running).
    """
    from planet_maiko.agents.coding_agent import cleanup_task_worktree, stop_agent_session
    task = db.get_or_404(Task, task_id)
    stopped = stop_agent_session(task_id)
    cleanup_task_worktree(task)
    db.session.delete(task)
    db.session.commit()
    return jsonify({"status": "deleted", "id": task_id, "agent_stopped": stopped})


@tasks_bp.route("/tasks/<task_id>/launch", methods=["POST"])
def launch_task(task_id):
    """Start the assigned coding agent's work on this task NOW.

    Kicks off the same headless agent flow as /agents/assign, but
    against the existing assignment — used by the "Launch" button on
    tasks that were assigned (e.g. via project plan approval) without
    a kickoff, or where the initial kickoff failed. Optional body:
    `{ plan_first: bool }` to override (defaults to whatever was
    captured on task.extra.plan_first, else False).

    Only coding agents need a manual launch — review/investigation
    agents run autonomously at assign time via /agents/assign.
    """
    from planet_maiko.agents.coding_agent import kickoff_coding_task

    task = db.get_or_404(Task, task_id)
    data = request.get_json(silent=True) or {}
    plan_first = data.get("plan_first")
    if plan_first is None:
        plan_first = bool((task.extra or {}).get("plan_first"))
    result = kickoff_coding_task(task, plan_first=bool(plan_first))
    if not result.get("success"):
        return jsonify({"error": result.get("error", "Launch failed")}), 400
    return jsonify({"mode": "coding", "launch_result": result}), 200


@tasks_bp.route("/tasks/<task_id>/reassign", methods=["POST"])
def reassign_task(task_id):
    """Reassign a task to a different agent.

    Body: { agent_id?: str }. If agent_id is omitted, the router picks a
    different agent of the same role for the task's scope, lazy-spawning
    if needed. If the same agent would be picked again, returns 409.
    """
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.orchestration import role_for_task, scope_for_task, find_profile, maybe_spawn

    task = db.get_or_404(Task, task_id)
    data = request.get_json(silent=True) or {}
    current = task.assigned_agent_id
    target_id = data.get("agent_id")

    if target_id:
        profile = db.session.get(AgentProfile, target_id)
        if not profile:
            return jsonify({"error": "agent not found"}), 404
        task.assigned_agent_id = profile.id
        _coerce_task_type_for_role(task, profile.role)
    else:
        # Auto-pick: force a new agent of the same role. Archive the
        # current so the router's simple find-match lookup skips it,
        # spawn a replacement.
        if current:
            old = db.session.get(AgentProfile, current)
            if old:
                old.archived = True
                old.archived_at = datetime.now(timezone.utc)
        role = role_for_task(task)
        scope = scope_for_task(task)
        found = find_profile(role, scope)
        new_profile = found or maybe_spawn(role, scope)
        if new_profile.id == current:
            return jsonify({"error": "no alternative agent available"}), 409
        task.assigned_agent_id = new_profile.id
        _coerce_task_type_for_role(task, new_profile.role)

    # Blow away the previous agent's worktree so the new agent
    # doesn't inherit stale TASK.md / commits. The cycle's
    # execute-agent-tasks phase (or a fresh assign) will prepare a
    # clean one for the new assignee. Clearing working_path + branch
    # on task.extra is what tells the cycle to prepare a new
    # worktree instead of re-using.
    from planet_maiko.agents.coding_agent import cleanup_task_worktree
    cleanup_task_worktree(task)
    new_extra = dict(task.extra or {})
    new_extra.pop("working_path", None)
    new_extra.pop("branch", None)
    new_extra.pop("session_id", None)
    task.extra = new_extra

    # Reset status so the new agent picks it up next cycle.
    if task.status == "in_progress":
        task.status = "new"
    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(task.to_dict())


def _coerce_task_type_for_role(task, role):
    """If assigning a review/investigation agent to a task whose type
    doesn't match one of the one-shot executable types, upgrade the
    type so the cycle's one-shot phase actually picks it up.

    Silent-fixes the common footgun: "I assigned an investigator to a
    todo-type task and nothing happened."
    """
    from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE
    if task.type in ONE_SHOT_ROLE_FOR_TYPE:
        return
    coerced = {"review": "review", "investigation": "investigation"}.get(role)
    if coerced:
        task.type = coerced


@tasks_bp.route("/tasks/<task_id>/linear", methods=["POST"])
def send_task_to_linear(task_id):
    """Create a Linear issue from a Maiko task.

    Stores the new Linear id/identifier/url on task.extra so the UI can
    deep-link and avoid creating duplicates.
    """
    from planet_maiko.pollers.linear_poller import LinearPoller

    task = db.get_or_404(Task, task_id)
    extra = dict(task.extra or {})

    # If this task is already linked to a Linear issue (either sent before
    # or imported from Linear originally), hand back what we have.
    if extra.get("linear_identifier") or extra.get("identifier"):
        return jsonify({
            "already_synced": True,
            "linear_id": extra.get("linear_id"),
            "linear_identifier": extra.get("linear_identifier") or extra.get("identifier"),
            "linear_url": extra.get("linear_url") or task.url,
        })

    data = request.get_json(silent=True) or {}

    # Description falls back to the originating pupdate's body.
    description = data.get("description")
    if description is None:
        description = extra.get("description") or ""
        if not description and task.source_pupdate is not None:
            description = task.source_pupdate.body or ""

    try:
        issue = LinearPoller.create_issue(
            task,
            description=description,
            team_id=data.get("team_id") or None,
            project_id=data.get("project_id") or None,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Linear create failed: {e}"}), 502

    extra["linear_id"] = issue["id"]
    extra["linear_identifier"] = issue["identifier"]
    extra["linear_url"] = issue["url"]
    task.extra = extra
    db.session.commit()
    return jsonify({
        "success": True,
        "linear_id": issue["id"],
        "linear_identifier": issue["identifier"],
        "linear_url": issue["url"],
    }), 201


@tasks_bp.route("/tasks/import-linear", methods=["POST"])
def import_from_linear():
    """Import assigned issues from Linear as tasks, plus led projects.

    Two passes so both bring-in-what-you-own and bring-in-what-you-lead
    happen from one click: issue import (creates tasks and member-role
    projects) then led-project import (adds any projects the viewer
    leads that don't show up via assigned issues).
    """
    from planet_maiko.config import load_config
    config = load_config()
    api_key = config.get("linear", {}).get("api_key")
    if not api_key:
        return jsonify({"error": "Linear API key not configured. Set it in Settings."}), 400

    from planet_maiko.pollers.linear_poller import LinearPoller
    poller = LinearPoller()
    stats = LinearPoller.import_issues(api_key)
    try:
        led = poller.import_led_projects(api_key)
        stats["projects_created"] = stats.get("projects_created", 0) + led.get("created", 0)
        stats["projects_updated"] = stats.get("projects_updated", 0) + led.get("updated", 0)
    except Exception as e:
        stats["led_projects_note"] = f"Led-project import failed: {e}"
    return jsonify(stats)
