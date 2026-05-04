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
    """Update task fields (title, priority, tags, metadata).

    Foreign-key-shaped columns (project_id, source_pupdate_id) get
    empty-string-to-None coercion so a cleared dropdown writes NULL
    instead of failing the FK constraint with a literal "" id.
    assigned_agent_id isn't FK at the DB level but follows the same
    convention so "Unassigned" works the same way.
    """
    task = db.get_or_404(Task, task_id)
    data = request.get_json()

    # Validate project_id exists when set, so the user gets a useful
    # error instead of a generic SQLite FK message.
    if data.get("project_id"):
        from planet_maiko.models.project import Project
        if not db.session.get(Project, data["project_id"]):
            return jsonify({"error": f"Project not found: {data['project_id']}"}), 400

    NULLABLE_ID_FIELDS = {"project_id", "source_pupdate_id", "assigned_agent_id"}

    for field in ["title", "type", "status", "priority", "url", "tags", "project_id", "assigned_agent_id", "due_date"]:
        if field not in data:
            continue
        value = data[field]
        if field in NULLABLE_ID_FIELDS and value == "":
            value = None
        setattr(task, field, value)
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


def _maybe_push_close_to_linear(task, target_state_type):
    """Opt-in: if task.extra.linear_sync_close is set, push a state
    update back to the linked Linear issue.

    target_state_type should be "completed" (for done) or "canceled"
    (for cancelled). We look up the team's first WorkflowState with
    that type — Linear workflow states are per-team, so we pick the
    default destination rather than hardcoding a stateId.

    Best-effort: any failure logs a warning and returns. We never
    fail the task-close because Linear sync misbehaved; the Maiko
    state change is the source of truth locally.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    extra = task.extra or {}
    if not extra.get("linear_sync_close"):
        return
    issue_id = extra.get("linear_id") or extra.get("linear_identifier")
    if not issue_id:
        return
    team_id = extra.get("linear_team_id")
    if not team_id:
        # Fallback to the configured team — works when the issue was
        # originally created against it (the common case).
        from planet_maiko.config import load_config
        team_id = (load_config().get("linear") or {}).get("team_id")
    if not team_id:
        _log.warning(f"[linear-sync] No team_id for task {task.id}, skipping")
        return

    try:
        from planet_maiko.pollers.linear_client import LinearClient
        client = LinearClient()
        meta = client.team_meta(team_id)
        target_state = next(
            (s for s in (meta.get("states") or [])
             if s.get("type") == target_state_type),
            None,
        )
        if not target_state:
            _log.warning(
                f"[linear-sync] Team {team_id} has no {target_state_type!r} state"
            )
            return
        client.update_issue(issue_id, stateId=target_state["id"])
        _log.info(
            f"[linear-sync] Pushed {target_state_type} to Linear issue "
            f"{extra.get('linear_identifier')} ({issue_id})"
        )
    except Exception as e:
        _log.warning(f"[linear-sync] close-sync failed for task {task.id}: {e}")


def _clear_task_dependents(task):
    """Orphan-clean rows that point at tasks.id before the delete.

    Two tables have FKs into tasks.id without an ON DELETE clause on
    the column, so with PRAGMA foreign_keys=ON SQLite blocks the
    delete even when the row's semantics say "OK to drop":
      - diff_comments.task_id (NOT NULL) — a comment that belongs to
        this task is meaningless without it; delete the rows.
      - agent_jobs.source_task_id (nullable) — the job may outlive
        the task (artifact-retention), so null out the pointer and
        leave the job row intact.

    Called from /tasks/<id>/done and /tasks/<id>/cancel before the
    db.session.delete(task) so the commit succeeds.
    """
    from planet_maiko.models.diff_comment import DiffComment
    from planet_maiko.models.agent_job import AgentJob

    DiffComment.query.filter_by(task_id=task.id).delete(synchronize_session=False)
    (AgentJob.query
        .filter_by(source_task_id=task.id)
        .update({"source_task_id": None}, synchronize_session=False))


@tasks_bp.route("/tasks/<task_id>/done", methods=["POST"])
def complete_task(task_id):
    """Mark a task as done — deletes it from the active list and
    removes any agent worktree backing it. The agent's last reply
    + its task.extra.artifact already capture the result; the
    worktree itself is just throwaway scratch space at this point."""
    from planet_maiko.agents.coding_agent import cleanup_task_worktree
    task = db.get_or_404(Task, task_id)
    # Push to Linear before we delete the task row (extra disappears with
    # the row). Best-effort — won't block the delete on Linear issues.
    _maybe_push_close_to_linear(task, "completed")
    cleanup_task_worktree(task)
    _clear_task_dependents(task)
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
    _maybe_push_close_to_linear(task, "canceled")
    cleanup_task_worktree(task)
    _clear_task_dependents(task)
    db.session.delete(task)
    db.session.commit()
    return jsonify({"status": "deleted", "id": task_id, "agent_stopped": stopped})


@tasks_bp.route("/tasks/<task_id>/launch", methods=["POST"])
def launch_task(task_id):
    """Start the assigned agent's work on this task NOW.

    Kicks off the same headless agent flow as /agents/assign, but
    against the existing assignment — used by the "Launch" button on
    tasks that were assigned (e.g. via project plan approval) without
    a kickoff, or where the initial kickoff failed or the agent was
    reassigned.

    Branches on task type:
      - Coding tasks → kickoff_coding_task (prep worktree if needed,
        start claude --print in it)
      - One-shot tasks (investigation / cartograph / repo_analysis /
        review) → prep worktree + _kickoff_agent_headless with the
        role's protocol

    Optional body: `{ plan_first: bool }` — only honored for coding
    tasks (one-shot agents don't have a plan step).
    """
    task = db.get_or_404(Task, task_id)
    data = request.get_json(silent=True) or {}
    plan_first = data.get("plan_first")
    if plan_first is None:
        plan_first = bool((task.extra or {}).get("plan_first"))

    result = _launch_task(task, plan_first=bool(plan_first))
    if not result.get("success"):
        return jsonify({"error": result.get("error", "Launch failed")}), 400
    return jsonify({"mode": result.get("mode", "coding"), "launch_result": result}), 200


def _launch_task(task, plan_first=False):
    """Route a launch to the right kickoff path based on task type.

    Returns {"success": bool, "error": str?, "mode": str, ...}.
    Commits the session on success — caller doesn't need to.
    """
    from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE

    if not task.assigned_agent_id:
        return {"success": False, "error": "no agent assigned"}

    if task.type in ONE_SHOT_ROLE_FOR_TYPE:
        return _launch_one_shot(task)

    from planet_maiko.agents.coding_agent import kickoff_coding_task
    result = kickoff_coding_task(task, plan_first=plan_first)
    result = dict(result or {})
    result.setdefault("mode", "coding")
    return result


def _launch_one_shot(task):
    """Prep worktree if missing, then kickoff the one-shot headless run.

    Mirrors the investigation/cartographer path in /agents/assign — the
    difference is we already have an assigned agent (so no profile
    lookup from the request) and we re-prep a fresh worktree if the
    previous one was cleared (e.g. after reassign).
    """
    import os
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.orchestration import resolve_repo_path, scope_for_task, build_task_prompt
    from planet_maiko.agents.coding_agent import prepare, _kickoff_agent_headless

    profile = db.session.get(AgentProfile, task.assigned_agent_id)
    if not profile:
        return {"success": False, "error": "assigned agent not found"}

    role = profile.role or "investigation"
    working_path = (task.extra or {}).get("working_path")

    if not working_path or not os.path.isdir(working_path):
        repo = scope_for_task(task)
        local_path = resolve_repo_path(repo)
        if not local_path:
            return {"success": False, "error": f"no local clone found for {repo or 'this task'}"}

        full_prompt = build_task_prompt(task, role, "")
        prep_result = prepare(
            task_id=task.id,
            task_title=task.title,
            prompt=full_prompt,
            repo_path=local_path,
            branch_prefix="maiko",
            agent_profile_id=profile.id,
            role=role,
        )
        if not prep_result:
            return {"success": False, "error": "failed to prepare worktree"}
        working_path = prep_result.get("working_path")
        extra = dict(task.extra or {})
        if working_path:
            extra["working_path"] = working_path
        if prep_result.get("branch"):
            extra["branch"] = prep_result.get("branch")
        task.extra = extra

    if task.status in ("new", "blocked"):
        task.status = "in_progress"
    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    kickoff = _kickoff_agent_headless(
        profile.id, working_path, task.id,
        branch_name=None, plan_first=False, role=role,
    )
    return {
        "success": True,
        "mode": role,
        "kickoff": kickoff,
        "working_path": working_path,
    }


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

    Accepts an optional body with any of:
      title, description, state_id, priority (0-4), cycle_id, project_id,
      label_ids[], assignee_id, parent_id, estimate, due_date (YYYY-MM-DD),
      team_id, sync_close (bool — opt-in to close the Linear issue when
      this task closes; off by default).
    """
    from planet_maiko.pollers.linear_client import LinearClient
    from planet_maiko.pollers.linear_poller import MAIKO_TO_LINEAR_PRIORITY
    from planet_maiko.config import load_config

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
    linear_cfg = (load_config().get("linear") or {})
    team_id = data.get("team_id") or linear_cfg.get("team_id")
    if not team_id:
        return jsonify({"error": "Linear team not configured — pick one in Settings"}), 400

    # Description falls back chain: explicit > task.extra.description >
    # originating pupdate body.
    description = data.get("description")
    if description is None:
        description = extra.get("description") or ""
        if not description and task.source_pupdate is not None:
            description = task.source_pupdate.body or ""
    description = description or None

    # Priority: explicit numeric > mapped from task priority name.
    priority = data.get("priority")
    if priority is None:
        priority = MAIKO_TO_LINEAR_PRIORITY.get(
            (task.priority or "normal").lower(), 3,
        )

    # Due date: explicit > task.due_date. Linear wants YYYY-MM-DD.
    due_date = data.get("due_date")
    if due_date is None and task.due_date is not None:
        try:
            due_date = task.due_date.isoformat()
        except Exception:
            due_date = None

    try:
        client = LinearClient()
        issue = client.create_issue(
            team_id=team_id,
            title=data.get("title") or task.title,
            description=description,
            stateId=data.get("state_id"),
            priority=priority,
            estimate=data.get("estimate"),
            assigneeId=data.get("assignee_id"),
            labelIds=data.get("label_ids") or None,
            cycleId=data.get("cycle_id"),
            projectId=data.get("project_id") or linear_cfg.get("default_project_id"),
            parentId=data.get("parent_id"),
            dueDate=due_date,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Linear create failed: {e}"}), 502

    extra["linear_id"] = issue.get("id")
    extra["linear_identifier"] = issue.get("identifier")
    extra["linear_url"] = issue.get("url")
    extra["linear_team_id"] = team_id
    # Persist cycle membership from the modal payload — the next
    # poll's sync_statuses() will keep it fresh if the issue moves
    # between cycles in Linear.
    if data.get("cycle_id"):
        extra["linear_cycle_id"] = data.get("cycle_id")
        if data.get("cycle_number") is not None:
            extra["linear_cycle_number"] = data.get("cycle_number")
        if data.get("cycle_name"):
            extra["linear_cycle_name"] = data.get("cycle_name")
    # Opt-in close-sync — persists on the task. /tasks/:id/done + cancel
    # check this flag before pushing a state update back to Linear.
    if data.get("sync_close"):
        extra["linear_sync_close"] = True
    task.extra = extra
    # Pin the URL onto task.url too so existing link affordances work.
    if not task.url and issue.get("url"):
        task.url = issue["url"]
    db.session.commit()
    return jsonify({
        "success": True,
        "linear_id": issue.get("id"),
        "linear_identifier": issue.get("identifier"),
        "linear_url": issue.get("url"),
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
