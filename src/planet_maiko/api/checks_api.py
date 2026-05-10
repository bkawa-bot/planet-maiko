"""HTTP surface for the repo-checker runner.

Two endpoints:

- GET  /api/checks?repo_path=...
    List the checks that would run (dry-detect). Used by the UI to
    show what's configured and let users point at a repo.

- POST /api/checks/run
    Body: {"repo_path": str, "task_id"?: str}
    Runs every detected/configured check inside repo_path and stores
    the result on the task if task_id is provided. Returns the full
    run payload.

Invoked by the `check_code` MCP tool an agent calls from its worktree
just before declaring `ready_for_review`. That ties review/coding
tasks to a checker run instead of agent self-report.
"""

import logging

from flask import Blueprint, jsonify, request

from planet_maiko.checks import detect_checks, run_checks
from planet_maiko.database import db
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

checks_bp = Blueprint("checks", __name__)


@checks_bp.route("/checks", methods=["GET"])
def list_checks():
    repo_path = (request.args.get("repo_path") or "").strip()
    if not repo_path:
        return jsonify({"error": "repo_path is required"}), 400
    return jsonify({"repo_path": repo_path, "checks": detect_checks(repo_path)})


@checks_bp.route("/checks/run", methods=["POST"])
def run_checks_route():
    """Run the worktree's checks (tests/lint/typecheck) plus LoRA.

    Accepts both `job_id` (canonical) and `task_id` (legacy) on the
    request body. The id is used to resolve a working_path when the
    caller didn't pass one explicitly.
    """
    data = request.get_json(silent=True) or {}
    repo_path = (data.get("repo_path") or "").strip()
    job_id = (data.get("job_id") or data.get("task_id") or "").strip()
    timeout = int(data.get("timeout") or 120)

    if not repo_path and job_id:
        # Resolve via AgentJob first, fall back to Task. The id can be
        # either post-unification — agents send their MAIKO_JOB_ID.
        from planet_maiko.models.agent_job import AgentJob
        job = db.session.get(AgentJob, job_id)
        if job and job.worktree_path:
            repo_path = job.worktree_path
        else:
            task = db.session.get(Task, job_id)
            if task and (task.extra or {}).get("working_path"):
                repo_path = (task.extra or {}).get("working_path")
    if not repo_path:
        return jsonify({"error": "repo_path required (or a job_id with a worktree)"}), 400

    try:
        result = run_checks(repo_path, timeout=timeout)
    except Exception as e:
        logger.exception("[checks] run failed: %s", e)
        return jsonify({"error": str(e)}), 500

    # Persist the latest run on the linked Task so the UI can surface
    # it and `ready_for_review` can be cross-checked against it. The
    # id from the wire is a job_id — resolve to the linked Task via
    # AgentJob.source_task_id when needed.
    persist_task_id = None
    if job_id:
        from planet_maiko.models.agent_job import AgentJob
        job = db.session.get(AgentJob, job_id)
        if job and job.source_task_id:
            persist_task_id = job.source_task_id
        else:
            persist_task_id = job_id  # fall back: id may already BE a Task.id
    if persist_task_id:
        task = db.session.get(Task, persist_task_id)
        if task is not None:
            extra = dict(task.extra or {})
            extra["check_results"] = {
                "summary": result.get("summary"),
                "checks": [
                    {k: v for k, v in c.items() if k != "command"}  # drop the command from the stored blob to keep it compact
                    for c in result.get("checks") or []
                ],
                "ran_at": _now_iso(),
            }
            task.extra = extra
            db.session.commit()

    return jsonify(result)


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
