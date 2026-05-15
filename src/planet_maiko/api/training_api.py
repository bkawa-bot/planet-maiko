"""Training API — LoRA fine-tuning from PR review history."""

import json
import logging

from flask import Blueprint, jsonify, request
from planet_maiko.database import db

logger = logging.getLogger(__name__)

training_bp = Blueprint("training", __name__)


@training_bp.route("/training/export-dataset", methods=["POST"])
def export_dataset():
    """Extract training data from PR review history."""
    from planet_maiko.brain.learning.training_data import extract_training_data
    from planet_maiko.config import load_config

    data = request.get_json(silent=True) or {}
    config = load_config()
    repos = data.get("repos") or config.get("github", {}).get("repos", [])
    limit = data.get("limit_per_repo", 200)

    if not repos:
        return jsonify({"error": "No repos configured"}), 400

    result = extract_training_data(repos=repos, limit_per_repo=limit)
    return jsonify(result)


@training_bp.route("/training/datasets", methods=["GET"])
def get_datasets():
    """List generated training datasets."""
    from planet_maiko.brain.learning.training_data import list_datasets
    return jsonify(list_datasets())


@training_bp.route("/training/dataset-stats", methods=["GET"])
def dataset_stats():
    """Get stats for the most recent dataset."""
    from planet_maiko.brain.learning.training_data import list_datasets, get_dataset_stats
    datasets = list_datasets()
    if not datasets:
        return jsonify({"total": 0})
    latest = datasets[0]
    stats = get_dataset_stats(latest["path"])
    stats["filename"] = latest["filename"]
    return jsonify(stats)


@training_bp.route("/training/train-agent", methods=["POST"])
def train_lora_endpoint():
    """Kick off a LoRA training job asynchronously.

    LoRAs are scoped per-repo (or "global" when no repo is given);
    there is no 1:1 between agents and adapters. Body fields:
      - repo: optional "org/name". Omitted → adapter name prefixed
        "lora-global-…" and treated as the fallback for any repo
        without a more specific adapter.
      - dataset_path: optional explicit JSONL dataset. Omitted → the
        trainer auto-picks a recent dataset (preferring repo-specific
        files when `repo` is set).
      - config: optional training hyperparameter overrides.

    Training takes 15-30 min, so we seed progress.json synchronously
    and hand the work to a daemon thread; the response is 202 with
    the adapter_name so the client knows which job it started.
    /training/progress reads the latest adapter's progress.json.
    """
    import os
    import threading as _threading
    from datetime import datetime, timezone
    from flask import current_app
    from planet_maiko.brain.learning.trainer import train_lora, check_requirements
    from planet_maiko.paths import data_dir

    data = request.get_json(silent=True) or {}
    repo = (data.get("repo") or "").strip() or None
    safe_repo = repo.replace("/", "--") if repo else "global"

    reqs = check_requirements()
    if not reqs["ready"]:
        return jsonify({
            "error": "Training backend not available",
            "recommendation": reqs.get("recommendation", ""),
            "details": reqs,
        }), 503

    models_dir = os.path.join(data_dir(), "models")
    os.makedirs(models_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    adapter_name = f"lora-{safe_repo}-{timestamp}"
    adapter_path = os.path.join(models_dir, adapter_name)
    os.makedirs(adapter_path, exist_ok=True)
    progress_path = os.path.join(adapter_path, "progress.json")

    started_at = datetime.now(timezone.utc).isoformat()
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump({
            "status": "preparing",
            "repo": repo,
            "scope": safe_repo,
            "adapter_name": adapter_name,
            "started_at": started_at,
            "iteration": 0,
            "total_iters": 0,
            "percent": 0,
        }, f)

    app = current_app._get_current_object()
    dataset_path = data.get("dataset_path")
    config = data.get("config")

    def _run():
        with app.app_context():
            try:
                result = train_lora(
                    repo=repo,
                    dataset_path=dataset_path,
                    config=config,
                    adapter_path=adapter_path,
                )
                if not result.get("success"):
                    # train_lora early-exits (no dataset, no backend)
                    # don't touch progress.json — write a failure state
                    # so the poller doesn't sit on "preparing" forever.
                    with open(progress_path, "w", encoding="utf-8") as pf:
                        json.dump({
                            "status": "failed",
                            "error": result.get("error", "Unknown error"),
                            "install_hint": result.get("install_hint"),
                            "percent": 0,
                        }, pf)
            except Exception as e:
                logger.exception("[training] Async train_lora crashed")
                try:
                    with open(progress_path, "w", encoding="utf-8") as pf:
                        json.dump({
                            "status": "failed",
                            "error": str(e),
                            "percent": 0,
                        }, pf)
                except Exception:
                    pass

    _threading.Thread(target=_run, daemon=True, name="train-lora").start()

    return jsonify({
        "status": "started",
        "adapter_name": adapter_name,
        "adapter_path": adapter_path,
        "scope": safe_repo,
    }), 202


@training_bp.route("/training/check-requirements", methods=["GET"])
def check_training_requirements():
    """Check if LoRA training is available on this machine."""
    from planet_maiko.brain.learning.trainer import check_requirements
    return jsonify(check_requirements())


@training_bp.route("/training/base-models", methods=["GET"])
def list_base_models():
    """Return the supported base models for the Train UI dropdown.
    Each adapter records its base in metadata.json so inference loads
    the matching weights + chat template — switching here is safe."""
    from planet_maiko.brain.learning.trainer import SUPPORTED_BASE_MODELS, DEFAULT_TRAINING_CONFIG
    return jsonify({
        "models": SUPPORTED_BASE_MODELS,
        "default": DEFAULT_TRAINING_CONFIG["base_model"],
    })


@training_bp.route("/training/progress", methods=["GET"])
def training_progress():
    """Poll training progress from the most recent adapter's progress.json."""
    import os
    from planet_maiko.paths import data_dir

    models_dir = os.path.join(data_dir(), "models")
    if not os.path.isdir(models_dir):
        return jsonify({"status": "idle"})

    adapters = sorted(os.listdir(models_dir), reverse=True)
    for adapter in adapters:
        progress_path = os.path.join(models_dir, adapter, "progress.json")
        if os.path.exists(progress_path):
            try:
                with open(progress_path) as f:
                    return jsonify(json.load(f))
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"[training] Could not read progress.json for {adapter}: {e}")

    return jsonify({"status": "idle"})


@training_bp.route("/training/adapters", methods=["GET"])
def list_adapters():
    """List all trained LoRA adapters."""
    import os
    from planet_maiko.paths import data_dir

    models_dir = os.path.join(data_dir(), "models")
    if not os.path.isdir(models_dir):
        return jsonify([])

    adapters = []
    for name in sorted(os.listdir(models_dir), reverse=True):
        path = os.path.join(models_dir, name)
        if not os.path.isdir(path):
            continue
        has_weights = os.path.exists(os.path.join(path, "adapters.safetensors"))
        adapters.append({
            "name": name,
            "path": path,
            "has_weights": has_weights,
        })

    return jsonify(adapters)


@training_bp.route("/training/assign-adapter", methods=["POST"])
def assign_adapter():
    """Assign an existing adapter to an agent profile."""
    from planet_maiko.models.agent_profile import AgentProfile

    data = request.get_json()
    profile_id = data.get("agent_profile_id")
    adapter_path = data.get("adapter_path")

    if not profile_id or not adapter_path:
        return jsonify({"error": "agent_profile_id and adapter_path required"}), 400

    profile = db.get_or_404(AgentProfile, profile_id)
    extra = profile.extra or {}
    extra["adapter_path"] = adapter_path
    profile.extra = extra
    db.session.commit()

    return jsonify({"status": "ok", "agent": profile_id, "adapter_path": adapter_path})


@training_bp.route("/training/rule-coverage", methods=["GET"])
def rule_coverage():
    """Show which active rules are already in training data.

    Query params:
        repo: filter to rules scoped to this repo (or global rules)
    """
    from planet_maiko.models.learning import Learning
    from planet_maiko.brain.learning.rule_training_data import get_covered_rule_ids
    from sqlalchemy import or_

    repo = request.args.get("repo") or None
    covered = get_covered_rule_ids(repo=repo)

    query = Learning.query.filter_by(status="active")
    if repo:
        query = query.filter(or_(
            Learning.scope_repo == repo,
            Learning.is_global == True,  # noqa: E712
            Learning.scope_repo.is_(None),
        ))
    active = query.all()

    # Also list available repos from learnings for the dropdown
    all_repos = sorted({
        l.scope_repo for l in Learning.query.filter_by(status="active").all() if l.scope_repo
    })

    covered_list = []
    uncovered_list = []
    for l in active:
        entry = {
            "id": l.id,
            "rule": l.rule,
            "category": l.category,
            "scope_repo": l.scope_repo,
            "confidence": l.confidence,
        }
        if l.id in covered:
            covered_list.append(entry)
        else:
            uncovered_list.append(entry)

    return jsonify({
        "active_count": len(active),
        "covered_count": len(covered_list),
        "uncovered_count": len(uncovered_list),
        "covered": covered_list,
        "uncovered": uncovered_list,
        "available_repos": all_repos,
        "filtered_repo": repo,
    })


# ---------------------------------------------------------------------------
# Rule dataset generation — async with a progress endpoint
# ---------------------------------------------------------------------------
#
# Generating training data calls Opus once per rule (plus synthesis for
# violations/passes). 30+ rules can easily blow past the HTTP timeout
# window, so we kick the work to a background thread and expose
# /training/generate-from-rules/progress for the UI to poll.

import threading as _threading
from datetime import datetime as _datetime, timezone as _tz

_rule_gen_state = {
    "status": "idle",          # idle | running | done | failed
    "started_at": None,
    "finished_at": None,
    "total_rules": 0,
    "rules_processed": 0,
    "current_rule": None,
    "pairs": 0,
    "errors": 0,
    "message": None,
    "file_path": None,
    "force": False,
    "repo": None,
}
_rule_gen_lock = _threading.Lock()


def _rule_gen_update(**kwargs):
    with _rule_gen_lock:
        _rule_gen_state.update(kwargs)


@training_bp.route("/training/generate-from-rules", methods=["POST"])
def generate_from_rules_endpoint():
    """Kick off an async rule-dataset generation job.

    Returns 202 Accepted immediately with the initial progress blob.
    Clients poll /training/generate-from-rules/progress for updates
    and the final result. Only one job may run at a time — a second
    POST while running returns 409 with the current progress.
    """
    from flask import current_app
    from planet_maiko.brain.learning.rule_training_data import generate_rule_dataset, get_covered_rule_ids
    from planet_maiko.models.learning import Learning
    from sqlalchemy import or_

    with _rule_gen_lock:
        if _rule_gen_state["status"] == "running":
            return jsonify({
                "error": "already running",
                "progress": dict(_rule_gen_state),
            }), 409

    data = request.get_json(silent=True) or {}
    examples = data.get("examples_per_rule", 50)
    force = data.get("force", False)
    repo = data.get("repo") or None
    # Style anchor — which repo's patterns to inject into the synth
    # prompt. Independent of `repo` (rule scope), so global rule runs
    # can be styled like a representative codebase. Empty / null =
    # fall back to `repo` (preserves prior behavior).
    style_anchor_repo = data.get("style_anchor_repo") or None
    # Cap at 10 — past that, Anthropic rate limits start mattering more
    # than additional parallelism helps. Floor at 1 so the user can
    # always step back to sequential if they're getting throttled.
    max_workers = max(1, min(10, int(data.get("max_workers", 5) or 5)))
    # Per-rule mix of violations vs passes. Default 0.6 = 60%
    # violations, 40% passes — counteracts the PASS-bias from
    # classification training. Clamped server-side too.
    violation_ratio = max(0.3, min(0.85, float(data.get("violation_ratio", 0.6) or 0.6)))

    # Incremental path stays synchronous when there's nothing to do.
    if not force:
        covered = get_covered_rule_ids(repo=repo)
        query = Learning.query.filter_by(status="active")
        if repo:
            query = query.filter(or_(
                Learning.scope_repo == repo,
                Learning.is_global == True,  # noqa: E712
                Learning.scope_repo.is_(None),
            ))
        active = query.all()
        new_ids = [l.id for l in active if l.id not in covered]
        if not new_ids:
            return jsonify({
                "success": True,
                "pairs": 0,
                "rules_processed": 0,
                "violations": 0,
                "passes": 0,
                "errors": 0,
                "message": "All active rules are already in training data. Use force=true to regenerate.",
            })
    else:
        new_ids = None

    _rule_gen_update(
        status="running",
        started_at=_datetime.now(_tz.utc).isoformat(),
        finished_at=None,
        total_rules=0,
        rules_processed=0,
        current_rule=None,
        pairs=0,
        errors=0,
        message=None,
        file_path=None,
        force=force,
        repo=repo,
    )

    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            try:
                result = generate_rule_dataset(
                    examples_per_rule=examples,
                    rule_ids=new_ids,
                    repo=repo,
                    progress_cb=_rule_gen_update,
                    max_workers=max_workers,
                    style_anchor_repo=style_anchor_repo,
                    violation_ratio=violation_ratio,
                )
                if result.get("success"):
                    _rule_gen_update(
                        status="done",
                        finished_at=_datetime.now(_tz.utc).isoformat(),
                        pairs=result.get("pairs", 0),
                        rules_processed=result.get("rules_processed", 0),
                        errors=result.get("errors", 0),
                        file_path=result.get("file_path"),
                        message=f"Generated {result.get('pairs', 0)} pairs from "
                                f"{result.get('rules_processed', 0)} rules",
                        current_rule=None,
                    )
                else:
                    _rule_gen_update(
                        status="failed",
                        finished_at=_datetime.now(_tz.utc).isoformat(),
                        message=result.get("error", "Generation failed"),
                        current_rule=None,
                    )
            except Exception as e:
                logger.exception("[training] Rule dataset generation crashed")
                _rule_gen_update(
                    status="failed",
                    finished_at=_datetime.now(_tz.utc).isoformat(),
                    message=str(e),
                    current_rule=None,
                )

    _threading.Thread(target=_run, daemon=True, name="rule-gen").start()

    return jsonify({
        "status": "started",
        "progress": dict(_rule_gen_state),
    }), 202


@training_bp.route("/training/generate-from-rules/progress", methods=["GET"])
def generate_from_rules_progress():
    """Current rule-dataset generation state. See the _rule_gen_state
    dict for fields. Safe to poll every few seconds.
    """
    with _rule_gen_lock:
        return jsonify(dict(_rule_gen_state))


@training_bp.route("/training/generate-synthetic", methods=["POST"])
def generate_synthetic_endpoint():
    """Generate synthetic training data by sending diffs to Opus."""
    from planet_maiko.brain.learning.synthetic_data import generate_synthetic_dataset

    data = request.get_json(silent=True) or {}

    # Release DB before long-running LLM calls
    db.session.close()

    result = generate_synthetic_dataset(
        input_dataset=data.get("input_dataset"),
        limit=data.get("limit"),
    )

    status = 200 if result.get("success") else 500
    return jsonify(result), status


@training_bp.route("/training/review", methods=["POST"])
def review_code_endpoint():
    """Review code using a trained LoRA adapter."""
    from planet_maiko.brain.learning.trainer import review_code

    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()

    if not code:
        return jsonify({"error": "code required"}), 400

    result = review_code(
        code=code,
        repo=data.get("repo"),
        adapter_path=data.get("adapter_path"),
        file_path=data.get("file_path"),
    )

    status = 200 if result.get("success") else 500
    return jsonify(result), status
