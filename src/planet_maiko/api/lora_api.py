"""LoRA review endpoints used by the agent via the maiko-channel MCP.

Three endpoints back the three MCP tools agents call:

- POST /lora/check       — run the repo's LoRA against a task's diff
- POST /lora/false_positive — record that a flagged line is actually fine
- POST /lora/false_negative — record that the model missed a real issue

Plus the Phase B read surface so external orchestrators can discover
what adapter (if any) Maiko holds for a repo:

- GET  /lora/adapters    — adapter metadata for a given repo

Repo → LoRA resolution lives in config.lora.models_by_repo. Agents
don't pick the model; their repo scope decides. If no model is
configured for a repo, /lora/check returns empty violations + a
no_model_for_repo flag so the agent can skip gracefully instead of
blocking on a model that doesn't exist.
"""

import json
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
# /lora/adapters — read surface for external orchestrators
# ---------------------------------------------------------------------------

def _read_adapter_metadata(adapter_path):
    """Read whatever adapter metadata we can find on disk.

    Returns a dict with trained_at / base_model / dataset_size /
    eval_score populated when the corresponding artifacts exist, or
    None for any field we can't pin down. No eval-result persistence
    exists today — eval_score always comes back None until we add one.
    """
    meta = {
        "trained_at": None,
        "base_model": None,
        "dataset_size": None,
        "eval_score": None,
    }

    # trained_at: prefer adapters.safetensors mtime (the real "done"
    # moment), fall back to the adapter directory's own mtime.
    weights_path = os.path.join(adapter_path, "adapters.safetensors")
    try:
        if os.path.exists(weights_path):
            ts = os.path.getmtime(weights_path)
        else:
            ts = os.path.getmtime(adapter_path)
        meta["trained_at"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except OSError:
        pass

    # base_model: MLX writes adapter_config.json with the base model
    # it was fine-tuned on. If it's missing, leave None — we don't
    # want to guess from DEFAULT_TRAINING_CONFIG because a given
    # adapter may have been trained with a different base.
    cfg_path = os.path.join(adapter_path, "adapter_config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            meta["base_model"] = cfg.get("base_model") or cfg.get("model") or None
        except (OSError, json.JSONDecodeError):
            pass

    # dataset_size: training writes train.jsonl into the adapter dir.
    train_path = os.path.join(adapter_path, "train.jsonl")
    if os.path.exists(train_path):
        try:
            with open(train_path, encoding="utf-8") as f:
                meta["dataset_size"] = sum(1 for line in f if line.strip())
        except OSError:
            pass

    return meta


@lora_bp.route("/lora/adapters", methods=["GET"])
def adapter_for_repo():
    """Return adapter metadata for a repo.

    Read surface so external orchestrators (MCP clients, other coding
    sessions) can discover whether Maiko holds a trained LoRA for a
    given repo and where it lives on disk. Repo → adapter resolution
    is the same config.lora.models_by_repo path /lora/check uses, so
    what this endpoint reports is what the agent would load.

    Query params:
        repo: "org/name". Required.

    Response 200:
        {
          "repo": "org/name",
          "exists": bool,
          "version": string | null,       // adapter directory name
          "trained_at": iso8601 | null,
          "base_model": string | null,
          "eval_score": float | null,     // null until eval persistence lands
          "dataset_size": int | null,
          "adapter_path": string | null   // absolute path (localhost use)
        }
    Response 400: when repo is missing.
    """
    repo = (request.args.get("repo") or "").strip()
    if not repo:
        return jsonify({"error": "repo is required"}), 400

    from planet_maiko.brain.learning.lora_eval import resolve_lora_for_repo
    adapter_path = resolve_lora_for_repo(repo)

    if not adapter_path or not os.path.isdir(adapter_path):
        return jsonify({
            "repo": repo,
            "exists": False,
            "version": None,
            "trained_at": None,
            "base_model": None,
            "eval_score": None,
            "dataset_size": None,
            "adapter_path": None,
        })

    meta = _read_adapter_metadata(adapter_path)
    return jsonify({
        "repo": repo,
        "exists": True,
        "version": os.path.basename(adapter_path.rstrip(os.sep)),
        "trained_at": meta["trained_at"],
        "base_model": meta["base_model"],
        "eval_score": meta["eval_score"],
        "dataset_size": meta["dataset_size"],
        "adapter_path": adapter_path,
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
