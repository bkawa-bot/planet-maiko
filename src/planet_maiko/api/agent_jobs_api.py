"""AgentJob CRUD — list, approve, cancel.

AgentJobs represent the pack's one-shot work (cartograph, investigation,
scheduled skills, etc.). Separate from Task, which is the user's own
todo / bug / feature list. See models/agent_job.py.
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.agent_job import AgentJob

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

    # Filter to jobs linked to a specific task — used by the legacy
    # /tasks/<id>/report redirect to find the unified /jobs/<id>
    # destination.
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


@agent_jobs_bp.route("/agent-jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    """Cancel a job. For pending_approval / queued: just mark cancelled.
    For running: terminate the subprocess + cleanup worktree."""
    j = db.get_or_404(AgentJob, job_id)
    if j.status == "running":
        # Reuse the coding-agent teardown. Safe no-op if session not tracked.
        try:
            from planet_maiko.agents.coding_agent import stop_agent_session
            stop_agent_session(job_id)
        except Exception:
            pass
        # Worktree cleanup if we have the path + branch
        if j.worktree_path and j.branch:
            try:
                from planet_maiko.agents.coding_agent import cleanup
                cleanup(j.worktree_path, j.branch)
            except Exception:
                pass
    j.status = "cancelled"
    j.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(j.to_dict())


@agent_jobs_bp.route("/agent-jobs/<job_id>", methods=["DELETE"])
def delete_job(job_id):
    """Hard delete. Use cancel for running jobs first; delete is for
    tidying up completed/cancelled history."""
    j = db.get_or_404(AgentJob, job_id)
    db.session.delete(j)
    db.session.commit()
    return jsonify({"deleted": job_id})


@agent_jobs_bp.route("/agent-jobs/<job_id>/ack", methods=["POST"])
def ack_job(job_id):
    """Mark a finished job's artifact as seen. The home_api Memos pane
    surfaces job_artifact rows for done jobs with no source_task and
    a non-null artifact, filtering out rows with extra.reviewed=True.
    Calling this on a job flips that bit so the row disappears from
    the pane — the canonical "dismiss" for job_artifact memos.

    No-op when the job isn't done — there's no point acknowledging an
    artifact that doesn't exist yet.
    """
    j = db.get_or_404(AgentJob, job_id)
    extra = dict(j.extra or {})
    extra["reviewed"] = True
    j.extra = extra
    db.session.commit()
    return jsonify({"job_id": job_id, "reviewed": True})
