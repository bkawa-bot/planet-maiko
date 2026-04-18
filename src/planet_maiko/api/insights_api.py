"""Insights API — CRUD for the Team Playbook.

Insights are tribal / operational notes the pack has surfaced about a
repo (tooling quirks, migration state, team conventions). Distinct
from Learnings — no LoRA training, no signal/cluster pipeline,
injected verbatim into every new agent's CLAUDE.md.

Endpoints:
    GET    /api/insights                — list (filter by repo, status)
    GET    /api/insights/playbook       — rendered playbook markdown + insights for a repo
    POST   /api/insights                — create (manual or agent-reported)
    PATCH  /api/insights/<id>           — update text / tags / expires_at
    POST   /api/insights/<id>/approve   — pending -> active
    POST   /api/insights/<id>/dismiss   — any -> dismissed
    POST   /api/insights/<id>/confirm   — bump last_confirmed_at
    DELETE /api/insights/<id>           — hard delete (rare; prefer dismiss)
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.insight import Insight

insights_bp = Blueprint("insights", __name__)


def _parse_expires(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


@insights_bp.route("/insights/playbook", methods=["GET"])
def playbook_for_repo():
    """Render the Repo Overview + Team Playbook for a repo.

    Read surface so external orchestrators (MCP clients, other coding
    sessions) can consume the exact same markdown + structured insight
    list that Maiko's own agent-prep path injects into CLAUDE.md.

    Query params:
        repo: "org/name" or bare repo name. Required. Insights are
              matched on the last path segment, so either form works.

    Response 200:
        {
          "repo": "org/name",
          "playbook": "<markdown block>",
          "insights": [ ... Insight.to_dict() ... ]
        }
    Response 400: when repo is missing.
    """
    repo = (request.args.get("repo") or "").strip()
    if not repo:
        return jsonify({"error": "repo is required"}), 400

    from planet_maiko.brain.learning.playbook import build_playbook
    result = build_playbook(repo)
    return jsonify({
        "repo": repo,
        "playbook": result["playbook_md"],
        "insights": result["insights"],
    })


@insights_bp.route("/insights", methods=["GET"])
def list_insights():
    """List insights. Filters: ?repo=org/name, ?status=active|pending|dismissed|all."""
    repo = request.args.get("repo")
    status = request.args.get("status", "active")

    q = Insight.query
    if status != "all":
        q = q.filter(Insight.status == status)
    if repo is not None:
        # "global" literal or empty -> only null scope; otherwise match
        # the repo or a suffix (org/name vs name).
        if repo in ("", "global"):
            q = q.filter(Insight.repo_scope.is_(None))
        else:
            from sqlalchemy import or_
            q = q.filter(
                or_(
                    Insight.repo_scope == repo,
                    Insight.repo_scope.like(f"%/{repo.split('/')[-1]}"),
                )
            )
    q = q.order_by(Insight.last_confirmed_at.desc())
    return jsonify([i.to_dict() for i in q.all()])


@insights_bp.route("/insights", methods=["POST"])
def create_insight():
    """Create an insight. Body: {text, repo_scope?, tags?, status?, author_agent_id?, expires_at?}.

    Status defaults to "active" for manual creates; agent-authored ones
    should pass status="pending" to land in the review queue first.
    """
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    insight = Insight(
        text=text,
        repo_scope=(data.get("repo_scope") or None),
        tags=data.get("tags") or [],
        status=data.get("status", "active"),
        author_agent_id=data.get("author_agent_id"),
        expires_at=_parse_expires(data.get("expires_at")),
    )
    db.session.add(insight)
    db.session.commit()
    return jsonify(insight.to_dict()), 201


@insights_bp.route("/insights/<int:insight_id>", methods=["PATCH"])
def update_insight(insight_id):
    insight = db.get_or_404(Insight, insight_id)
    data = request.get_json() or {}
    if "text" in data:
        insight.text = (data["text"] or "").strip()
    if "repo_scope" in data:
        insight.repo_scope = data["repo_scope"] or None
    if "tags" in data:
        insight.tags = data["tags"] or []
    if "expires_at" in data:
        insight.expires_at = _parse_expires(data["expires_at"])
    insight.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(insight.to_dict())


@insights_bp.route("/insights/<int:insight_id>/approve", methods=["POST"])
def approve_insight(insight_id):
    insight = db.get_or_404(Insight, insight_id)
    insight.status = "active"
    insight.last_confirmed_at = datetime.now(timezone.utc)

    # Only one active Repo Overview per repo — approving a new overview
    # automatically supersedes the prior one. (Bullet-style insights
    # stack, so this rule is limited to the "overview" tag.)
    if insight.tags and "overview" in insight.tags:
        others = Insight.query.filter(
            Insight.id != insight.id,
            Insight.status == "active",
            Insight.repo_scope == insight.repo_scope,
        ).all()
        for o in others:
            if o.tags and "overview" in o.tags:
                o.status = "dismissed"

    db.session.commit()
    return jsonify(insight.to_dict())


@insights_bp.route("/insights/cartograph", methods=["POST"])
def cartograph_repo():
    """Spawn a one-shot Cartographer agent to draft a Repo Overview
    insight for the given repo.

    The agent walks the tree read-only, produces a structured overview,
    and replies via MCP as a pending insight tagged ["overview",
    "cartographer"]. User approves in the Playbook UI; approving
    supersedes any prior overview for the same repo.
    """
    import uuid as _uuid
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.agents.coding_agent import (
        prepare, _kickoff_agent_headless,
    )
    from planet_maiko.api.agents_api import _build_task_prompt
    from planet_maiko.orchestration import resolve_repo_path

    data = request.get_json() or {}
    repo = (data.get("repo") or "").strip()
    if not repo:
        return jsonify({"error": "repo is required"}), 400

    local_path = resolve_repo_path(repo)
    if not local_path:
        return jsonify({"error": f"No local clone found for {repo}"}), 400

    # Find or seed a cartographer profile. One is enough — cartography
    # runs don't need per-repo specialization the way coding pups do.
    profile = (AgentProfile.query
               .filter(AgentProfile.role == "cartographer",
                       (AgentProfile.archived.is_(False)) | (AgentProfile.archived.is_(None)))
               .first())
    if not profile:
        profile = AgentProfile(
            id=f"cartographer-{_uuid.uuid4().hex[:6]}",
            display_name="Atlas",
            avatar="fox",
            flavor_text="Walks new repos and draws the map.",
            role="cartographer",
            scope_repo=None,
        )
        db.session.add(profile)
        db.session.flush()

    task = Task(
        id=f"task-{_uuid.uuid4().hex[:10]}",
        title=f"Cartograph {repo}",
        type="cartograph",
        priority="normal",
        status="new",
        assigned_agent_id=profile.id,
        extra={"repo": repo, "cartograph": True},
    )
    db.session.add(task)
    db.session.commit()

    full_prompt = _build_task_prompt(task, "cartographer", "")
    try:
        result = prepare(
            task_id=task.id,
            task_title=task.title,
            prompt=full_prompt,
            repo_path=local_path,
            branch_prefix="cartographer",
            auto_kickoff=False,
            use_worktree=True,
            agent_profile_id=profile.id,
            role="cartographer",
        )
    except Exception as e:
        return jsonify({"error": f"Cartographer preparation failed: {e}"}), 500
    if not result:
        return jsonify({"error": "Failed to prepare cartographer"}), 500

    working_path = result.get("working_path")
    _kickoff_agent_headless(
        profile.id, working_path, task.id,
        branch_name=None,
        plan_first=False,
        role="cartographer",
    )

    return jsonify({
        "task_id": task.id,
        "profile_id": profile.id,
        "profile_name": profile.display_name,
        "working_path": working_path,
    }), 201


@insights_bp.route("/insights/<int:insight_id>/dismiss", methods=["POST"])
def dismiss_insight(insight_id):
    insight = db.get_or_404(Insight, insight_id)
    insight.status = "dismissed"
    db.session.commit()
    return jsonify(insight.to_dict())


@insights_bp.route("/insights/<int:insight_id>/confirm", methods=["POST"])
def confirm_insight(insight_id):
    """Bump last_confirmed_at — the user (or an agent) just verified
    this insight is still true. Stops the UI from fading it as stale
    and re-sorts it to the top of the playbook."""
    insight = db.get_or_404(Insight, insight_id)
    insight.last_confirmed_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(insight.to_dict())


@insights_bp.route("/insights/<int:insight_id>", methods=["DELETE"])
def delete_insight(insight_id):
    insight = db.get_or_404(Insight, insight_id)
    db.session.delete(insight)
    db.session.commit()
    return jsonify({"status": "deleted", "id": insight_id})
