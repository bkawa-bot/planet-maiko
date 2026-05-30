"""CRUD for AgentType — the role-level identity of an agent.

Companion to /api/specialties (which manages per-run prompt bodies).
Together they replace /api/skills, which conflated both concepts on
the underlying CustomSkill model.

The four built-ins (coding / review / investigation / cartographer)
appear in the list like any user-created type. Deleting a default
soft-deletes (deleted_at tombstone so the seed pass on next boot
doesn't resurrect); deleting a user-created type hard-deletes.
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.agent_type import AgentType

agent_types_bp = Blueprint("agent_types", __name__)


_EDITABLE_FIELDS = {
    "name", "description", "icon",
    "protocol_prompt",
    "spawn_mode", "permission_mode",
    "output_kind", "input_kind", "accepts",
    "model_routing_key",
    "extra",
}


def _coerce(field, value):
    """Coerce request-body field values to the column type. Null-
    collapse for empty strings on nullable string columns."""
    if field in ("permission_mode", "description"):
        # Empty string from a UI textarea collapses to NULL so reader
        # code can rely on "null => use default" semantics.
        return value or None
    return value


@agent_types_bp.route("/agent-types", methods=["GET"])
def list_agent_types():
    """List all active (non-tombstoned) agent types, defaults first.

    Defaults render in the original seeded order (coding, review,
    investigation, cartographer) via id-based sort within is_default;
    user-created types follow in alpha order.
    """
    rows = (
        AgentType.query
        .filter(AgentType.deleted_at.is_(None))
        .order_by(AgentType.is_default.desc(), AgentType.id)
        .all()
    )
    return jsonify([r.to_dict() for r in rows])


@agent_types_bp.route("/agent-types/<type_id>", methods=["GET"])
def get_agent_type(type_id):
    row = db.session.get(AgentType, type_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "AgentType not found"}), 404
    return jsonify(row.to_dict())


@agent_types_bp.route("/agent-types", methods=["POST"])
def create_agent_type():
    """Create a user-defined agent type.

    Required: id, name, protocol_prompt. Everything else falls back
    to model defaults — sensible for a "I just want a custom role
    with my own protocol" workflow.
    """
    data = request.get_json() or {}
    for required in ("id", "name", "protocol_prompt"):
        if not data.get(required):
            return jsonify({"error": f"{required} is required"}), 400

    if db.session.get(AgentType, data["id"]) is not None:
        return jsonify({"error": "id already exists"}), 409

    row = AgentType(
        id=data["id"],
        name=data["name"],
        description=data.get("description") or None,
        icon=data.get("icon") or "user",
        is_default=False,
        protocol_prompt=data["protocol_prompt"],
        spawn_mode=data.get("spawn_mode") or "worktree",
        permission_mode=data.get("permission_mode") or None,
        output_kind=data.get("output_kind") or "diff",
        input_kind=data.get("input_kind") or "task",
        accepts=data.get("accepts") or [data.get("input_kind") or "task"],
        model_routing_key=data.get("model_routing_key") or "coding_agent",
        extra=data.get("extra") or {},
    )
    db.session.add(row)
    db.session.commit()
    return jsonify(row.to_dict()), 201


@agent_types_bp.route("/agent-types/<type_id>", methods=["PATCH"])
def update_agent_type(type_id):
    """Partial update. PATCHing any field on a default flips
    user_edited=True so the next boot's seed pass stops refreshing
    the row from the bundled spec."""
    row = db.session.get(AgentType, type_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "AgentType not found"}), 404

    data = request.get_json() or {}
    touched = False
    for field in _EDITABLE_FIELDS:
        if field in data:
            setattr(row, field, _coerce(field, data[field]))
            touched = True

    if touched and row.is_default:
        row.user_edited = True

    db.session.commit()
    return jsonify(row.to_dict())


@agent_types_bp.route("/agent-types/<type_id>", methods=["DELETE"])
def delete_agent_type(type_id):
    """Delete an agent type.

    Defaults soft-delete (deleted_at tombstone so the boot seed pass
    knows to skip; user can revive by clearing deleted_at). Custom
    types hard-delete. Defers checking whether any AgentProfile still
    references the id — that's a separate concern; profiles silently
    fall back to "coding" when their role no longer resolves.
    """
    row = db.session.get(AgentType, type_id)
    if row is None:
        return jsonify({"error": "AgentType not found"}), 404
    if row.is_default:
        row.deleted_at = datetime.now(timezone.utc)
    else:
        db.session.delete(row)
    db.session.commit()
    return jsonify({"status": "deleted"})
