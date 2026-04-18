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
    data = request.get_json(silent=True) or {}
    repo_path = (data.get("repo_path") or "").strip()
    task_id = (data.get("task_id") or "").strip()
    timeout = int(data.get("timeout") or 120)

    if not repo_path:
        # Fall back to the task's working_path if the agent didn't send one.
        if task_id:
            task = db.session.get(Task, task_id)
            if task and (task.extra or {}).get("working_path"):
                repo_path = (task.extra or {}).get("working_path")
    if not repo_path:
        return jsonify({"error": "repo_path required (or a task_id with extra.working_path)"}), 400

    try:
        result = run_checks(repo_path, timeout=timeout)
    except Exception as e:
        logger.exception("[checks] run failed: %s", e)
        return jsonify({"error": str(e)}), 500

    # Also run the LoRA verifier for this task when an adapter is
    # configured for the repo. The LoRA is just another verifier with
    # pass/fail output — surfacing it alongside mechanical checks lets
    # agents react to both in one place instead of calling two tools.
    # Violations are returned structured so the agent can still record
    # lora_false_positive / lora_false_negative feedback on specific
    # items when they disagree with the model.
    lora_result = None
    if task_id:
        try:
            from planet_maiko.api.lora_api import run_lora_for_task
            lora_result = run_lora_for_task(task_id=task_id, scope="branch")
        except Exception as e:
            logger.warning(f"[checks] LoRA verifier failed: {e}")
            lora_result = {"error": str(e)}

    if lora_result is not None:
        result["lora"] = lora_result
        # Hoist "lora found violations" into the summary so the agent's
        # blocked flag reflects it. Missing adapter / no changes / hard
        # errors don't count as failures — only substantive violations do.
        lora_violations = lora_result.get("violations") or []
        if lora_violations:
            summary = result.get("summary") or {}
            summary["lora_violations"] = len(lora_violations)
            summary["blocked"] = True
            result["summary"] = summary

    # Persist the latest run on the task so the UI can surface it and
    # `ready_for_review` can be cross-checked against it.
    if task_id:
        task = db.session.get(Task, task_id)
        if task is not None:
            extra = dict(task.extra or {})
            extra["check_results"] = {
                "summary": result.get("summary"),
                "checks": [
                    {k: v for k, v in c.items() if k != "command"}  # drop the command from the stored blob to keep it compact
                    for c in result.get("checks") or []
                ],
                "lora": lora_result,
                "ran_at": _now_iso(),
            }
            task.extra = extra
            db.session.commit()

    return jsonify(result)


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
