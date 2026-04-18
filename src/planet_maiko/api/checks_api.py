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

from planet_maiko.checks import append_check, detect_checks, run_checks
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
                "ran_at": _now_iso(),
            }
            task.extra = extra
            db.session.commit()

    return jsonify(result)


@checks_bp.route("/checks/promote-learning", methods=["POST"])
def promote_learning():
    """Promote a Learning to a concrete check in a repo's .maiko/checks.json.

    The ladder: Learning (informal rule the LoRA has internalized) →
    explicit command that fails when the rule is violated → enforced
    on every future check_code() call. Caller supplies the command
    (typically a grep with `!` negation, a custom shell script, or a
    one-off linter invocation). Maiko doesn't try to infer the
    command — the user sees the rule text and decides what "a
    violation of this rule" looks like at the shell level.

    Body:
        {
          "learning_id": int,   // required — links the check back to the Learning
          "repo_path": str,     // required — where the .maiko/checks.json lives
          "name": str,          // required — displayed in check_code output
          "command": str        // required — shell command that exits non-zero on violation
        }

    Stamps {promoted_learnings: [learning_id, ...]} onto the
    Learning's metadata so the UI can show "already promoted" without
    reading every repo's checks.json.
    """
    from planet_maiko.models.learning import Learning

    data = request.get_json(silent=True) or {}
    learning_id = data.get("learning_id")
    repo_path = (data.get("repo_path") or "").strip()
    name = (data.get("name") or "").strip()
    command = (data.get("command") or "").strip()

    if not learning_id or not repo_path or not name or not command:
        return jsonify({"error": "learning_id, repo_path, name, command all required"}), 400

    learning = db.session.get(Learning, learning_id)
    if learning is None:
        return jsonify({"error": f"Learning {learning_id} not found"}), 404

    try:
        updated = append_check(repo_path, name, command)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("[checks] promote-learning failed")
        return jsonify({"error": str(e)}), 500

    # We intentionally don't stamp anything onto the Learning itself —
    # the schema has no extra column, and the source of truth is the
    # repo's own .maiko/checks.json. Re-promoting de-dups by matching
    # command text, so "already promoted" just means the same entry
    # updates in place rather than piling up.
    logger.info(
        f"[checks] promoted learning {learning_id} to check '{name}' in {repo_path}"
    )

    return jsonify({
        "success": True,
        "repo_path": repo_path,
        "learning_id": learning.id,
        "checks": updated.get("checks", []),
    })


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
