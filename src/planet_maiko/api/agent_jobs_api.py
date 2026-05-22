"""AgentJob CRUD — list, approve, cancel.

AgentJobs represent the pack's one-shot work (cartograph, investigation,
scheduled skills, etc.). Separate from Task, which is the user's own
todo / bug / feature list. See models/agent_job.py.
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.agent_job import AgentJob

logger = logging.getLogger(__name__)

agent_jobs_bp = Blueprint("agent_jobs", __name__)


@agent_jobs_bp.route("/agent-jobs", methods=["GET"])
def list_jobs():
    """List agent jobs. Query params:
      status=<status>[,<status>...]  — filter (default: active-ish: not done+not cancelled)
      scope_repo=<repo>
      include_done=true              — include finished jobs
      limit=50
    """
    q = AgentJob.query

    status_param = request.args.get("status")
    if status_param:
        statuses = [s.strip() for s in status_param.split(",") if s.strip()]
        q = q.filter(AgentJob.status.in_(statuses))
    elif request.args.get("include_done", "false").lower() != "true":
        q = q.filter(AgentJob.status.in_(["pending_approval", "queued", "running"]))

    scope_repo = request.args.get("scope_repo")
    if scope_repo:
        q = q.filter(AgentJob.scope_repo == scope_repo)

    # Filter to jobs linked to a specific task. Used by the
    # /tasks/<id>/report redirect to find the /jobs/<id> destination.
    source_task_id = request.args.get("source_task_id")
    if source_task_id:
        q = q.filter(AgentJob.source_task_id == source_task_id)

    limit = min(int(request.args.get("limit") or 100), 500)
    jobs = q.order_by(AgentJob.created_at.desc()).limit(limit).all()
    return jsonify([j.to_dict() for j in jobs])


@agent_jobs_bp.route("/agent-jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    j = db.get_or_404(AgentJob, job_id)
    return jsonify(j.to_dict())


@agent_jobs_bp.route("/agent-jobs/<job_id>/approve", methods=["POST"])
def approve_job(job_id):
    """Move a pending_approval job to queued. The cycle's execute phase
    picks it up next tick and kicks off the agent."""
    j = db.get_or_404(AgentJob, job_id)
    if j.status != "pending_approval":
        return jsonify({"error": f"job is {j.status}, not pending_approval"}), 400
    j.status = "queued"
    j.approved_at = datetime.now(timezone.utc)
    j.approved_by = "user"
    db.session.commit()
    return jsonify(j.to_dict())


@agent_jobs_bp.route("/agent-jobs/quick-launch", methods=["POST"])
def quick_launch():
    """Direct-launch an AgentJob with a user-picked agent and prompt.

    Mints a fresh Task + queued AgentJob in one round-trip. Bypasses the
    pack-router LLM hop entirely (the user already knows who they want).

    Body:
      agent_profile_id: str (required) — which agent runs it
      title:            str (required) — short prompt / job title
      description:      str (optional) — full task body, defaults to title
      task_type:        str (default "coding") — coding | review | investigation | cartograph | repo_analysis
      scope_repo:       str (optional) — org/repo; falls back to profile.scope_repo
      priority:         str (default "normal")
      specialty_id:     str (optional) — CustomSkill id to layer on this run

    Returns: {"task": {...}, "job": {...}}
    """
    import uuid as _uuid
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import resolve_repo_path

    data = request.get_json(silent=True) or {}
    agent_id = (data.get("agent_profile_id") or "").strip()
    title = (data.get("title") or "").strip()
    if not agent_id:
        return jsonify({"error": "agent_profile_id is required"}), 400
    if not title:
        return jsonify({"error": "title is required"}), 400

    profile = db.session.get(AgentProfile, agent_id)
    if not profile or profile.archived:
        return jsonify({"error": f"Agent {agent_id!r} not found or archived"}), 404

    task_type = (data.get("task_type") or "coding").strip() or "coding"
    description = (data.get("description") or "").strip()
    priority = (data.get("priority") or "normal").strip() or "normal"
    scope_repo = (data.get("scope_repo") or "").strip() or profile.scope_repo or None
    specialty_id = (data.get("specialty_id") or "").strip() or None

    task_extra = {
        "description": description or title,
        "source": "quick-launch",
    }
    if scope_repo:
        task_extra["repo"] = scope_repo
    if specialty_id:
        task_extra["specialty_id"] = specialty_id

    task_id = f"task-ql-{_uuid.uuid4().hex[:10]}"
    task = Task(
        id=task_id,
        title=title,
        type=task_type,
        status="in_progress",
        priority=priority,
        assigned_agent_id=profile.id,
        tags=["quick-launch"],
        extra=task_extra,
    )
    db.session.add(task)
    db.session.flush()

    job_extra = {}
    local_path = resolve_repo_path(scope_repo) if scope_repo else None
    if local_path:
        job_extra["repo_path"] = local_path
    if specialty_id:
        job_extra["specialty_id"] = specialty_id

    job_id = f"job-{_uuid.uuid4().hex[:10]}"
    job = AgentJob(
        id=job_id,
        kind=task_type,
        title=title,
        description=description or None,
        scope_repo=scope_repo,
        priority=priority,
        created_by="quick-launch",
        source_task_id=task_id,
        agent_profile_id=profile.id,
        requires_approval=False,
        approved_at=datetime.now(timezone.utc),
        approved_by="user",
        status="queued",
        extra=job_extra,
    )
    db.session.add(job)

    # Link the job onto the task's extra so /tasks views can find it
    # the same way they do for tasks launched through the regular path.
    task_extra["agent_job_id"] = job_id
    task.extra = task_extra
    db.session.commit()

    logger.info(
        f"[quick-launch] task={task_id} job={job_id} agent={profile.id} "
        f"kind={task_type} repo={scope_repo!r}"
    )
    return jsonify({"task": task.to_dict(), "job": job.to_dict()}), 201


@agent_jobs_bp.route("/agent-jobs/<job_id>/change-kind", methods=["POST"])
def change_kind(job_id):
    """Switch a running job's kind (investigation → coding, etc.).

    Useful when the agent's investigation surfaces work that wants
    coding, or vice versa. Updates job.kind, cascades to the linked
    Task.type so the UI reflects the switch, and returns the new
    role's agent-protocol markdown so the calling CLI can print the
    fresh instructions and the agent can adopt them mid-session
    without a restart.

    Body: {"kind": str}
    Response: {previous_kind, kind, protocol}
    """
    import pathlib

    VALID_KINDS = {"coding", "investigation", "review", "cartograph", "repo_analysis"}
    # Roles with dedicated protocols; everything else falls back to the
    # generic agent-protocol.md the coding role also uses.
    SPECIFIC_PROTOCOLS = {
        "investigation": "investigation-agent-protocol",
        "review": "review-agent-protocol",
        "cartograph": "cartographer-agent-protocol",
    }

    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or "").strip()
    if kind not in VALID_KINDS:
        return jsonify({
            "error": f"Unknown kind: {kind!r}. Pick one of {sorted(VALID_KINDS)}.",
        }), 400

    job = db.get_or_404(AgentJob, job_id)
    previous_kind = job.kind
    job.kind = kind

    # Cascade to the linked Task so the UI reflects the role switch
    # (Tasks page badge, etc.). Skip terminal tasks.
    if job.source_task_id:
        from planet_maiko.models.task import Task
        task = db.session.get(Task, job.source_task_id)
        if task is not None and task.status not in ("done", "cancelled"):
            task.type = kind
            task.updated_at = datetime.now(timezone.utc)

    db.session.commit()

    protocol_name = SPECIFIC_PROTOCOLS.get(kind, "agent-protocol")
    protocol_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "prompts" / f"{protocol_name}.md"
    )
    try:
        protocol_text = protocol_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"[change-kind] couldn't load protocol {protocol_name}: {e}")
        protocol_text = ""

    logger.info(f"[change-kind] {job_id}: {previous_kind!r} -> {kind!r}")
    return jsonify({
        "job_id": job_id,
        "previous_kind": previous_kind,
        "kind": kind,
        "protocol": protocol_text,
    })


@agent_jobs_bp.route("/agent-jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    """Soft-cancel a job. Stops the subprocess but keeps the worktree
    and session_id around so the user can revive.

    Worktree cleanup is deferred to the shutdown ritual or an
    explicit forget call (the misclick cost on a long-running job is
    high). Revive flips status back to running so the user can resume.
    """
    j = db.get_or_404(AgentJob, job_id)
    if j.status == "running":
        try:
            from planet_maiko.agents.runtime import stop_agent_session
            stop_agent_session(job_id)
        except Exception:
            pass
    j.status = "cancelled"
    j.finished_at = datetime.now(timezone.utc)
    # Cascade cancel to the linked Task so the Tasks page also stops
    # showing this work as in-flight. Two skips: terminal tasks (never
    # un-close a done one), and tasks that still have other in-flight
    # jobs working on them. Cancelling one of N parallel agents
    # shouldn't kill the task they all share.
    if j.source_task_id:
        from planet_maiko.models.task import Task
        t = db.session.get(Task, j.source_task_id)
        if t is not None and t.status not in ("done", "cancelled"):
            active_siblings = AgentJob.query.filter(
                AgentJob.source_task_id == j.source_task_id,
                AgentJob.id != j.id,
                AgentJob.status.in_(("queued", "running")),
            ).count()
            if active_siblings == 0:
                t.status = "cancelled"
                t.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(j.to_dict())


@agent_jobs_bp.route("/agent-jobs/<job_id>/retry", methods=["POST"])
def retry_job(job_id):
    """Re-queue a failed AgentJob so the next cycle re-runs it.

    Flips status from `failed` → `queued`, clears the error and
    finished_at, and leaves the worktree (if one exists) intact.
    execute_jobs.py's "if not job.worktree_path" guard means a job
    that failed before prepare() finished will get a fresh worktree
    on retry, while one that failed at kickoff or later resumes the
    existing one — both right.

    Refuses on non-failed jobs so a misclick doesn't requeue a
    running or already-completed run.
    """
    j = db.get_or_404(AgentJob, job_id)
    if j.status != "failed":
        return jsonify({"error": f"Job is {j.status}, not failed"}), 400
    j.status = "queued"
    j.error = None
    j.finished_at = None
    db.session.commit()
    logger.info(f"[retry] {job_id} re-queued for next cycle")
    return jsonify(j.to_dict())


@agent_jobs_bp.route("/agent-jobs/<job_id>/revive", methods=["POST"])
def revive_job(job_id):
    """Bring a cancelled job back. Flips status to running and keeps
    the worktree intact. The next chat message or wake call resumes
    the claude session.

    Refuses if the worktree was cleaned up — there's nothing left to
    resume into; the user starts fresh.
    """
    import os
    j = db.get_or_404(AgentJob, job_id)
    if j.status != "cancelled":
        return jsonify({"error": f"Job is {j.status}, not cancelled"}), 400
    if j.worktree_path and not os.path.isdir(j.worktree_path):
        return jsonify({
            "error": "Worktree was cleaned up — can't revive. Start a fresh job.",
        }), 410
    j.status = "running"
    j.finished_at = None
    # Cascade revive to the linked Task so it leaves the cancelled
    # state too. Only flip if the task is currently cancelled — don't
    # disturb done or in-flight states.
    if j.source_task_id:
        from planet_maiko.models.task import Task
        t = db.session.get(Task, j.source_task_id)
        if t is not None and t.status == "cancelled":
            t.status = "in_progress"
            t.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(j.to_dict())


@agent_jobs_bp.route("/agent-jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    """Hard delete a job — cleans the worktree if one is still on disk
    and removes the row. The "I'm sure, this is gone for good" escape
    hatch from the soft-cancel pattern.

    Refuses on running jobs to keep the cancel-first workflow honest;
    a running job's worktree shouldn't get yanked while the claude
    process is still writing into it.
    """
    j = db.get_or_404(AgentJob, job_id)
    if j.status == "running":
        return jsonify({
            "error": "Job is running — cancel it first, then delete",
        }), 400
    if j.worktree_path and j.branch:
        try:
            from planet_maiko.agents.runtime import cleanup
            cleanup(j.worktree_path, j.branch)
        except Exception as e:
            logger.warning(f"[delete-job] worktree cleanup failed for {job_id}: {e}")
    db.session.delete(j)
    db.session.commit()
    return jsonify({"deleted": job_id})


