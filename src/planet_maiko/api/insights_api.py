"""Insights API — CRUD for the Team Playbook.

Insights are tribal / operational notes the pack has surfaced about a
repo (tooling quirks, migration state, team conventions). Distinct
from Learnings — no LoRA training, no signal/cluster pipeline,
injected verbatim into every new agent's CLAUDE.md.

Endpoints:
    GET    /api/insights                — list (filter by repo, status)
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
    db.session.commit()
    return jsonify(insight.to_dict())


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
