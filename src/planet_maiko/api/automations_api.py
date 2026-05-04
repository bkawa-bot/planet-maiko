"""Automations CRUD + list endpoints.

The unified when/then table. Replaces the old /goals surface. See
brain/automations/engine.py for what the stored kinds mean.
"""

from flask import Blueprint, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.automation import Automation

automations_bp = Blueprint("automations", __name__)


_ALLOWED_STATUSES = {"active", "paused", "archived"}


def _payload_to_fields(data, *, allow_create=True):
    """Read a request body into a dict of Automation column updates.

    Empty/missing fields fall through so PATCH can update only what
    the client sent. For create (POST), when[]/then[]/name are
    required by the caller before this helper runs.
    """
    out = {}
    for field in ("name", "description"):
        if field in data:
            out[field] = data[field]
    if "when" in data:
        out["when"] = data["when"] or []
    if "when_logic" in data:
        val = (data["when_logic"] or "all").lower()
        out["when_logic"] = "any" if val == "any" else "all"
    if "within_minutes" in data:
        out["within_minutes"] = int(data["within_minutes"]) if data["within_minutes"] else None
    if "then" in data:
        out["then"] = data["then"] or []
    if "status" in data and data["status"] in _ALLOWED_STATUSES:
        out["status"] = data["status"]
    if "agent_profile_id" in data:
        out["agent_profile_id"] = data["agent_profile_id"] or None
    if "scope_repo" in data:
        out["scope_repo"] = (data["scope_repo"] or "").strip() or None
    if "cooldown_days" in data:
        out["cooldown_days"] = int(data["cooldown_days"]) if data["cooldown_days"] else 0
    return out


@automations_bp.route("/automations", methods=["GET"])
def list_automations():
    """List automations. Query params:
      scope_repo=<repo>      — filter by repo
      status=<status>        — filter (default: all except archived)
      agent_profile_id=<id>  — filter to a specific agent's watches
    """
    q = Automation.query
    scope_repo = request.args.get("scope_repo")
    if scope_repo:
        q = q.filter(Automation.scope_repo == scope_repo)
    status = request.args.get("status")
    if status:
        q = q.filter(Automation.status == status)
    else:
        q = q.filter(Automation.status != "archived")
    agent_profile_id = request.args.get("agent_profile_id")
    if agent_profile_id:
        # Include both profile-specific + role-wide rows (null profile_id)
        # so a profile card shows the full set of watches it would respond to.
        q = q.filter(
            db.or_(
                Automation.agent_profile_id == agent_profile_id,
                Automation.agent_profile_id.is_(None),
            )
        )
    rows = q.order_by(Automation.scope_repo.asc().nulls_last(), Automation.id.asc()).all()
    return jsonify([a.to_dict() for a in rows])


@automations_bp.route("/automations/<int:automation_id>", methods=["GET"])
def get_automation(automation_id):
    a = db.get_or_404(Automation, automation_id)
    return jsonify(a.to_dict())


@automations_bp.route("/automations", methods=["POST"])
def create_automation():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    if not isinstance(data.get("when"), list) or not data["when"]:
        return jsonify({"error": "when[] is required and must be non-empty"}), 400
    if not isinstance(data.get("then"), list) or not data["then"]:
        return jsonify({"error": "then[] is required and must be non-empty"}), 400
    fields = _payload_to_fields(data)
    fields.setdefault("status", "active")
    fields.setdefault("created_by", "user")
    fields.setdefault("cooldown_days", int(data.get("cooldown_days") or 7))
    a = Automation(**fields)
    db.session.add(a)
    db.session.commit()
    return jsonify(a.to_dict()), 201


@automations_bp.route("/automations/<int:automation_id>", methods=["PATCH"])
def update_automation(automation_id):
    a = db.get_or_404(Automation, automation_id)
    data = request.get_json(silent=True) or {}
    for k, v in _payload_to_fields(data, allow_create=False).items():
        setattr(a, k, v)
    db.session.commit()
    return jsonify(a.to_dict())


@automations_bp.route("/automations/<int:automation_id>", methods=["DELETE"])
def delete_automation(automation_id):
    a = db.get_or_404(Automation, automation_id)
    # Seeded automations (from core seeders or plugins) respawn on
    # the next boot if hard-deleted — the seeder's "does a row with
    # this key already exist?" check sees nothing and creates a new
    # one. User experience: "I turned this off but it came back."
    # Archive instead so the dedup check still fires. Archived is
    # filtered from the default list view, so to the user it looks
    # gone. They can explicitly unearth + re-activate from the
    # archived filter if they change their mind.
    is_seeded = (
        a.created_by == "seed"
        or (a.created_by or "").startswith("plugin:")
    )
    if is_seeded:
        a.status = "archived"
        db.session.commit()
        return jsonify({
            "archived": automation_id,
            "note": "Seeded automation archived — won't re-seed on restart.",
        })
    db.session.delete(a)
    db.session.commit()
    return jsonify({"deleted": automation_id})


