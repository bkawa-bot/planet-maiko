"""LoRA review endpoints used by the agent via the maiko-channel MCP.

Three endpoints back the three MCP tools agents call:

- POST /lora/check       — run the repo's LoRA against a task's diff
- POST /lora/false_positive — record that a flagged line is actually fine
- POST /lora/false_negative — record that the model missed a real issue

Repo → LoRA resolution lives in config.lora.models_by_repo. Agents
don't pick the model; their repo scope decides. If no model is
configured for a repo, /lora/check returns empty violations + a
no_model_for_repo flag so the agent can skip gracefully instead of
blocking on a model that doesn't exist.
"""

import logging
import os
import re
import subprocess
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

lora_bp = Blueprint("lora", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VIOLATION_RE = re.compile(
    r"VIOLATION\s*:?\s*"
    r"(?:\[\s*(?P<category>[a-z_]+)\s*\])?\s*"
    r"(?P<message>.+)",
    re.IGNORECASE,
)


def _parse_violations(output):
    """Turn the model's free-form output into a structured list.

    The LoRA was trained to emit "PASS" or lines like
    "VIOLATION: [category] description" per file. This scans the
    output line-by-line; unrecognized text is ignored. Returns a
    list of {category, severity, message} dicts — file/line are
    filled in by the caller since the model's output doesn't
    reliably include them at a per-line granularity.
    """
    out = (output or "").strip()
    if not out or out.upper().startswith("PASS"):
        return []
    violations = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.upper() == "PASS":
            continue
        match = _VIOLATION_RE.search(line)
        if not match:
            continue
        violations.append({
            "category": (match.group("category") or "pattern").lower(),
            "severity": "suggestion",
            "message": match.group("message").strip(),
        })
    return violations


def _git(args, cwd, timeout=30):
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def _default_branch(worktree_path):
    try:
        rc, out, _ = _git(
            ["symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
            cwd=worktree_path, timeout=5,
        )
        if rc == 0 and out.strip():
            return out.strip().split("/", 1)[-1]
    except Exception:
        pass
    return "main"


# ---------------------------------------------------------------------------
# /lora/check
# ---------------------------------------------------------------------------

@lora_bp.route("/lora/check", methods=["POST"])
def lora_check():
    """Run the task's LoRA against its worktree diff.

    Body:
      {
        "task_id": "task-123",          // required
        "scope":  "branch" | "last_commit"  // optional, default "branch"
      }

    Response:
      {
        "violations": [{file, line, category, severity, message}],
        "model_path": "/path/to/adapter",
        "scope": "branch" | "last_commit",
        "no_model_for_repo": bool   // true → agent skips without error
      }
    """
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    scope = data.get("scope") or "branch"

    if not task_id:
        return jsonify({"error": "task_id is required"}), 400
    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": f"Task {task_id} not found"}), 404

    extra = task.extra or {}
    worktree = extra.get("working_path")
    if not worktree or not os.path.isdir(worktree):
        return jsonify({"error": "Task has no worktree"}), 400

    repo = extra.get("repo") or extra.get("repository")
    if not repo:
        # Fall back to parsing task.url
        from planet_maiko.orchestration import scope_for_task
        repo = scope_for_task(task)

    from planet_maiko.brain.learning.lora_eval import resolve_lora_for_repo
    adapter_path = resolve_lora_for_repo(repo)
    if not adapter_path:
        return jsonify({
            "violations": [],
            "model_path": None,
            "scope": scope,
            "no_model_for_repo": True,
            "repo": repo,
        })

    # Compute the diff the agent wants to review.
    if scope == "last_commit":
        base_ref = "HEAD~1"
    else:
        base_branch = _default_branch(worktree)
        rc, merge_base, _ = _git(
            ["merge-base", "HEAD", f"origin/{base_branch}"], cwd=worktree,
        )
        if rc != 0:
            rc, merge_base, _ = _git(
                ["merge-base", "HEAD", base_branch], cwd=worktree,
            )
        base_ref = merge_base.strip() if rc == 0 else base_branch

    rc, diff, derr = _git(
        ["diff", "--no-color", f"{base_ref}..HEAD"],
        cwd=worktree, timeout=60,
    )
    if rc != 0:
        return jsonify({"error": f"git diff failed: {derr.strip()[:200]}"}), 500
    if not diff.strip():
        return jsonify({
            "violations": [],
            "model_path": adapter_path,
            "scope": scope,
            "no_changes": True,
        })

    from planet_maiko.brain.learning.trainer import review_code
    result = review_code(code=diff, adapter_path=adapter_path)
    if not result.get("success"):
        return jsonify({
            "error": result.get("error") or "LoRA inference failed",
            "model_path": adapter_path,
        }), 500

    violations = _parse_violations(result.get("output", ""))
    return jsonify({
        "violations": violations,
        "model_path": adapter_path,
        "scope": scope,
        "raw_output": result.get("output", ""),
        "ran_at": datetime.now(timezone.utc).isoformat(),
    })


# ---------------------------------------------------------------------------
# Feedback endpoints
# ---------------------------------------------------------------------------

@lora_bp.route("/lora/false_positive", methods=["POST"])
def lora_false_positive():
    """Record a corrective PASS — LoRA flagged this but it's actually fine.

    Body: {code, file (optional), category (optional), repo (optional), reason (optional)}.
    Wraps brain.learning.feedback.add_corrective_pass — same storage
    the maiko lora-feedback CLI uses, so this feeds the next retrain.
    """
    data = request.get_json(silent=True) or {}
    code = data.get("code")
    if not code:
        return jsonify({"error": "code is required"}), 400
    from planet_maiko.brain.learning.feedback import add_corrective_pass
    result = add_corrective_pass(
        code=code,
        file_path=data.get("file"),
        repo=data.get("repo"),
        model_output=data.get("reason"),
    )
    status = 200 if result.get("success") else 500
    return jsonify(result), status


@lora_bp.route("/lora/false_negative", methods=["POST"])
def lora_false_negative():
    """Record a corrective VIOLATION — LoRA missed a real issue.

    Body: {code, violation, category (optional), file (optional), repo (optional)}.
    Wraps brain.learning.feedback.add_corrective_violation — same
    storage the maiko lora-miss CLI uses.
    """
    data = request.get_json(silent=True) or {}
    code = data.get("code")
    violation = data.get("violation")
    if not code or not violation:
        return jsonify({"error": "code and violation are required"}), 400
    from planet_maiko.brain.learning.feedback import add_corrective_violation
    result = add_corrective_violation(
        code=code,
        violation=violation,
        category=data.get("category"),
        file_path=data.get("file"),
        repo=data.get("repo"),
    )
    status = 200 if result.get("success") else 500
    return jsonify(result), status
