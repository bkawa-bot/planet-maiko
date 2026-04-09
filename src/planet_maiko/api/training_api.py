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
def train_agent_endpoint():
    """Train a LoRA adapter for an agent using extracted PR data."""
    from planet_maiko.brain.learning.trainer import train_agent, check_requirements

    data = request.get_json(silent=True) or {}
    agent_id = data.get("agent_profile_id")

    if not agent_id:
        return jsonify({"error": "agent_profile_id required"}), 400

    reqs = check_requirements()
    if not reqs["ready"]:
        return jsonify({
            "error": "Training backend not available",
            "recommendation": reqs.get("recommendation", ""),
            "details": reqs,
        }), 503

    result = train_agent(
        agent_profile_id=agent_id,
        dataset_path=data.get("dataset_path"),
        repo=data.get("repo"),
        config=data.get("config"),
    )

    status = 200 if result.get("success") else 500
    return jsonify(result), status


@training_bp.route("/training/check-requirements", methods=["GET"])
def check_training_requirements():
    """Check if LoRA training is available on this machine."""
    from planet_maiko.brain.learning.trainer import check_requirements
    return jsonify(check_requirements())


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
            except Exception:
                pass

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
    """Show which active rules are already in training data and which aren't."""
    from planet_maiko.models.learning import Learning
    from planet_maiko.brain.learning.rule_training_data import get_covered_rule_ids

    covered = get_covered_rule_ids()
    active = Learning.query.filter_by(status="active").all()

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
    })


@training_bp.route("/training/generate-from-rules", methods=["POST"])
def generate_from_rules_endpoint():
    """Generate training data from active learnings (incremental by default)."""
    from planet_maiko.brain.learning.rule_training_data import generate_rule_dataset, get_covered_rule_ids
    from planet_maiko.models.learning import Learning

    data = request.get_json(silent=True) or {}
    examples = data.get("examples_per_rule", 50)
    force = data.get("force", False)

    # Release DB before long LLM call
    db.session.close()

    if force:
        result = generate_rule_dataset(examples_per_rule=examples)
    else:
        # Incremental: only generate for rules not already covered
        covered = get_covered_rule_ids()
        active = Learning.query.filter_by(status="active").all()
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

        result = generate_rule_dataset(examples_per_rule=examples, rule_ids=new_ids)

    status = 200 if result.get("success") else 500
    return jsonify(result), status


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
        agent_profile_id=data.get("agent_profile_id"),
        adapter_path=data.get("adapter_path"),
        file_path=data.get("file_path"),
    )

    status = 200 if result.get("success") else 500
    return jsonify(result), status
