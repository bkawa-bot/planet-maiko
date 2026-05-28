"""CRUD for Specialty — swappable per-run prompt bodies.

Companion to /api/agent-types. A Specialty is what gets appended to
CLAUDE.md as "Your specialty for this run" when the user picks one at
assign time. Many specialties can be attached to a profile via
AgentProfile.specialty_ids.

Together with /api/agent-types this replaces the historical /api/skills
endpoint (which conflated specialties + agent types on the underlying
CustomSkill model). /api/skills stays available as a back-compat read
shim that lists specialties — phase 4 of the refactor will remove it.
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.specialty import Specialty

specialties_bp = Blueprint("specialties", __name__)


@specialties_bp.route("/specialties", methods=["GET"])
def list_specialties():
    """List all non-tombstoned specialties, defaults first."""
    rows = (
        Specialty.query
        .filter(Specialty.deleted_at.is_(None))
        .order_by(Specialty.is_default.desc(), Specialty.name)
        .all()
    )
    return jsonify([r.to_dict() for r in rows])


@specialties_bp.route("/specialties/<spec_id>", methods=["GET"])
def get_specialty(spec_id):
    row = db.session.get(Specialty, spec_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "Specialty not found"}), 404
    return jsonify(row.to_dict())


@specialties_bp.route("/specialties", methods=["POST"])
def create_specialty():
    """Create a user-defined specialty.

    Required: id, name, prompt. icon defaults to "wand", mcps to [].
    """
    data = request.get_json() or {}
    for required in ("id", "name", "prompt"):
        if not data.get(required):
            return jsonify({"error": f"{required} is required"}), 400

    if db.session.get(Specialty, data["id"]) is not None:
        return jsonify({"error": "id already exists"}), 409

    row = Specialty(
        id=data["id"],
        name=data["name"],
        description=data.get("description") or None,
        prompt=data["prompt"],
        mcps=list(data.get("mcps") or []),
        icon=data.get("icon") or "wand",
        is_default=False,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify(row.to_dict()), 201


@specialties_bp.route("/specialties/<spec_id>", methods=["PATCH"])
def update_specialty(spec_id):
    """Partial update. PATCHing prompt on a default flips
    user_edited=True so the boot seed pass stops re-syncing.
    """
    row = db.session.get(Specialty, spec_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "Specialty not found"}), 404

    data = request.get_json() or {}
    if "name" in data:
        row.name = data["name"]
    if "description" in data:
        row.description = data["description"] or None
    if "prompt" in data:
        row.prompt = data["prompt"]
        row.user_edited = True
    if "mcps" in data:
        row.mcps = list(data["mcps"] or [])
    if "icon" in data:
        row.icon = data["icon"] or "wand"

    db.session.commit()
    return jsonify(row.to_dict())


@specialties_bp.route("/specialties/<spec_id>", methods=["DELETE"])
def delete_specialty(spec_id):
    """Delete a specialty.

    Defaults soft-delete (tombstone for the seed pass); user-created
    rows hard-delete. Doesn't unattach from AgentProfile.specialty_ids
    lists — callers can prune those independently when they fetch.
    """
    row = db.session.get(Specialty, spec_id)
    if row is None:
        return jsonify({"error": "Specialty not found"}), 404
    if row.is_default:
        row.deleted_at = datetime.now(timezone.utc)
    else:
        db.session.delete(row)
    db.session.commit()
    return jsonify({"status": "deleted"})
