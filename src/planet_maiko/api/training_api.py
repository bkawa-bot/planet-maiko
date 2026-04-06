"""Training API — run training sessions to build agent context sets."""

import json
import logging
import random
import subprocess

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.models.learning import Learning
from planet_maiko.models.tournament import Tournament, TournamentEntry

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
    """Get stats for a specific dataset."""
    from planet_maiko.brain.learning.training_data import list_datasets, get_dataset_stats
    datasets = list_datasets()
    if not datasets:
        return jsonify({"total": 0})
    # Return stats for the most recent dataset
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

    # Check if training is possible
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


@training_bp.route("/training/history", methods=["GET"])
def training_history():
    """List past training sessions."""
    sessions = Tournament.query.filter_by(status="training").order_by(
        Tournament.created_at.desc()
    ).limit(20).all()
    return jsonify([t.to_dict() for t in sessions])


@training_bp.route("/training/prs", methods=["GET"])
def list_merged_prs():
    """List recent merged PRs from configured repos for training."""
    from planet_maiko.config import load_config
    config = load_config()
    repos = config.get("github", {}).get("repos", [])

    if not repos:
        return jsonify({"error": "No repos configured in Settings > GitHub"}), 400

    prs = []
    for repo in repos:
        try:
            result = subprocess.run(
                ["gh", "pr", "list", "--repo", repo, "--state", "merged",
                 "--limit", "15", "--json", "number,title,mergedAt"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                for pr in json.loads(result.stdout):
                    prs.append({
                        "repo": repo,
                        "number": pr["number"],
                        "title": pr["title"],
                        "merged_at": pr.get("mergedAt"),
                    })
        except Exception as e:
            logger.warning(f"[training] Failed to list PRs for {repo}: {e}")

    prs.sort(key=lambda p: p.get("merged_at") or "", reverse=True)
    return jsonify(prs[:30])


@training_bp.route("/training/run", methods=["POST"])
def run_training():
    """Run a training session: test learning combos against a merged PR.

    For pups (empty context_set): tests 3 random combos.
    For specialists: tests variations of the current set.
    """
    from flask import current_app

    data = request.get_json()
    repo = data.get("repo")
    pr_number = data.get("pr_number")
    agent_id = data.get("agent_profile_id")

    if not repo or not pr_number or not agent_id:
        return jsonify({"error": "repo, pr_number, and agent_profile_id required"}), 400

    profile = db.session.get(AgentProfile, agent_id)
    if not profile:
        return jsonify({"error": "Agent not found"}), 404

    # Fetch PR data
    pr_data = _fetch_pr(repo, pr_number)
    if not pr_data:
        return jsonify({"error": "Could not fetch PR data. Is gh CLI configured?"}), 400

    # Classify task tags
    tags = _classify_tags(pr_data)

    # Get all active learnings
    all_learnings = Learning.query.filter_by(status="active").all()
    if not all_learnings:
        return jsonify({"error": "No active learnings. Run 'Backfill from PRs' on the Knowledge page first."}), 400

    all_ids = [l.id for l in all_learnings]
    current_set = profile.context_set or []

    # Build combos to test
    combos = _build_combos(current_set, all_ids)

    # Score each combo
    runtime = _get_runtime()
    if not runtime:
        return jsonify({"error": "Claude Code runtime not available"}), 503

    entries = []
    for combo in combos:
        brief = _build_brief(combo)
        prompt = f"""You are a coding agent solving this task. Describe your approach and write the code you would produce.

{f"Follow these coding guidelines:{chr(10)}{brief}" if brief else ""}

## Task
{pr_data['task']}

Write the actual code changes you would make. Include file paths, function signatures, and implementation details. Be specific."""

        from planet_maiko.agents.routing import resolve_model
        result = runtime.send(prompt, timeout=180, model=resolve_model("training:entry"))
        entries.append({
            "name": combo["name"],
            "learning_ids": combo["ids"],
            "output": result.get("output", "")[:3000] if result.get("success") else "",
        })

    # LLM-as-judge
    _score_entries(runtime, entries, pr_data["diff"])

    # Find winner
    best = max(entries, key=lambda e: e.get("score", 0))

    # Update agent's context set
    old_set = list(current_set)
    profile.context_set = list(best["learning_ids"])

    # Update specialization scores
    if tags:
        specs = dict(profile.specializations or {})
        for tag in tags:
            spec_key = f"{repo}:{tag}"
            current = specs.get(spec_key, 0.0)
            specs[spec_key] = min(1.0, current + 0.05)
        profile.specializations = specs

    # Persist as a tournament record (status="training" to distinguish)
    tournament = Tournament(
        pr_repo=repo,
        pr_number=int(pr_number),
        pr_title=pr_data["title"],
        pr_diff_summary=pr_data["diff"][:2000],
        task_description=pr_data["task"][:1000],
        task_tags=tags,
        status="training",
        winning_strategy=best["name"],
        completed_at=datetime.now(timezone.utc),
    )
    db.session.add(tournament)
    db.session.flush()

    for entry in entries:
        te = TournamentEntry(
            tournament_id=tournament.id,
            strategy=entry["name"],
            learning_ids=entry.get("learning_ids", []),
            agent_profile_id=agent_id,
            output=entry.get("output", "")[:3000],
            score=entry.get("score"),
            judge_reasoning=entry.get("reason", ""),
        )
        db.session.add(te)

    db.session.commit()

    return jsonify({
        "pr": {"repo": repo, "number": pr_number, "title": pr_data["title"]},
        "tags": tags,
        "agent": profile.display_name,
        "entries": entries,
        "winner": best["name"],
        "context_set_before": old_set,
        "context_set_after": list(best["learning_ids"]),
    })


def _fetch_pr(repo, pr_number):
    """Fetch PR data via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "title,body,files"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)

        diff_result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", repo],
            capture_output=True, text=True, timeout=15,
        )
        diff = diff_result.stdout[:5000] if diff_result.returncode == 0 else ""

        files = data.get("files", [])
        file_names = [f.get("path", f.get("filename", "")) for f in files] if isinstance(files, list) else []

        return {
            "title": data.get("title", ""),
            "body": data.get("body", ""),
            "diff": diff,
            "task": f"Make changes to {', '.join(file_names[:10])} in {repo}. "
                    f"Goal: {data.get('title', '')}. {(data.get('body') or '')[:500]}",
        }
    except Exception as e:
        logger.warning(f"[training] PR fetch failed: {e}")
        return None


def _classify_tags(pr_data):
    """Quick tag classification from title + files."""
    title = (pr_data.get("title") or "").lower()
    tags = []
    keyword_map = {
        "security": ["auth", "security", "jwt", "token", "permission", "xss", "injection"],
        "testing": ["test", "spec", "mock", "fixture"],
        "performance": ["perf", "cache", "optimize", "slow", "fast", "memory"],
        "bug-fix": ["fix", "bug", "patch", "hotfix", "resolve"],
        "api": ["api", "endpoint", "route", "handler"],
        "database": ["migration", "schema", "query", "database", "sql"],
        "frontend": ["component", "css", "ui", "layout", "style"],
        "refactoring": ["refactor", "cleanup", "rename", "reorganize"],
    }
    for tag, keywords in keyword_map.items():
        if any(k in title for k in keywords):
            tags.append(tag)
    return tags[:4] or ["general"]


def _build_combos(current_set, all_ids):
    """Build learning combos to test."""
    combos = [{"name": "baseline", "ids": []}]

    if not current_set:
        # Pup: 3 random combos
        for i in range(3):
            sample_size = min(5, len(all_ids))
            ids = random.sample(all_ids, sample_size)
            combos.append({"name": f"random-{i+1}", "ids": ids})
    else:
        # Specialist: test variations
        combos.append({"name": "current", "ids": list(current_set)})

        # Try adding a candidate
        unused = [lid for lid in all_ids if lid not in current_set]
        if unused:
            candidate = random.choice(unused)
            combos.append({"name": "current+new", "ids": list(current_set) + [candidate]})

        # Try dropping the least confident
        if len(current_set) > 1:
            learnings_in_set = Learning.query.filter(Learning.id.in_(current_set)).all()
            weakest = min(learnings_in_set, key=lambda l: l.confidence) if learnings_in_set else None
            if weakest:
                reduced = [lid for lid in current_set if lid != weakest.id]
                combos.append({"name": "current-weakest", "ids": reduced})

    return combos


def _build_brief(combo):
    """Build markdown brief from learning IDs."""
    if not combo["ids"]:
        return ""
    learnings = Learning.query.filter(Learning.id.in_(combo["ids"])).all()
    return "\n".join(f"- {l.rule}" for l in learnings)


def _score_entries(runtime, entries, actual_diff):
    """LLM-as-judge scoring."""
    outputs_text = ""
    for i, entry in enumerate(entries):
        preview = (entry.get("output") or "")[:1000]
        outputs_text += f"\n--- Entry {i+1}: {entry['name']} ---\n{preview}\n"

    prompt = f"""You are judging a code competition. Score each entry 0-10 based on how closely it matches the actual merged code.

ACTUAL merged code:
{actual_diff[:3000]}

Entries:
{outputs_text}

Respond with JSON: {{"scores": [{{"name": "entry_name", "score": N, "reason": "brief reason"}}]}}"""

    from planet_maiko.agents.routing import resolve_model
    result = runtime.send_json(prompt, timeout=120, model=resolve_model("training:judge"))

    if result.get("parsed") and "scores" in result["parsed"]:
        for score_data in result["parsed"]["scores"]:
            for entry in entries:
                if entry["name"] == score_data.get("name"):
                    entry["score"] = score_data.get("score", 0)
                    entry["reason"] = score_data.get("reason", "")
                    break
    else:
        for entry in entries:
            entry["score"] = 5.0
            entry["reason"] = "Could not judge (LLM unavailable)"


def _get_runtime():
    """Get the configured runtime."""
    try:
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime()
        return runtime if runtime and runtime.is_available() else None
    except Exception:
        return None
