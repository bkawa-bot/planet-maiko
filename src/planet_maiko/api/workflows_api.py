"""CRUD for Workflow — saved flow-editor graphs. See models/workflow.py.

JSON-blob storage: the whole canvas is one `graph` field, PATCHed as a
unit whenever the editor saves. Running a workflow (a WorkflowRun plus a
per-node AgentJob) is a later phase; this blueprint is create / read /
update / delete of the saved graph only. Mirrors agent_types_api.py.
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.workflow import Workflow

workflows_bp = Blueprint("workflows", __name__)


_EDITABLE_FIELDS = {"name", "description", "graph", "extra"}


@workflows_bp.route("/workflows", methods=["GET"])
def list_workflows():
    """List active (non-tombstoned) workflows, most recently edited first."""
    rows = (
        Workflow.query
        .filter(Workflow.deleted_at.is_(None))
        .order_by(Workflow.updated_at.desc())
        .all()
    )
    return jsonify([r.to_dict() for r in rows])


@workflows_bp.route("/workflows/<wf_id>", methods=["GET"])
def get_workflow(wf_id):
    row = db.session.get(Workflow, wf_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "Workflow not found"}), 404
    return jsonify(row.to_dict())


@workflows_bp.route("/workflows", methods=["POST"])
def create_workflow():
    """Create a workflow. Only `name` is required; the editor saves the
    full graph on the first PATCH, so a fresh workflow starts empty."""
    data = request.get_json() or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400

    row = Workflow(
        name=data["name"],
        description=data.get("description") or None,
        graph=data.get("graph") or {"nodes": [], "edges": []},
        extra=data.get("extra") or {},
    )
    db.session.add(row)
    db.session.commit()
    return jsonify(row.to_dict()), 201


@workflows_bp.route("/workflows/<wf_id>", methods=["PATCH"])
def update_workflow(wf_id):
    """Whole-canvas save. The editor PATCHes the full `graph` (plus any
    renamed name/description) on a debounced save."""
    row = db.session.get(Workflow, wf_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "Workflow not found"}), 404

    data = request.get_json() or {}
    for field in _EDITABLE_FIELDS:
        if field in data:
            value = data[field]
            # Empty description collapses to NULL for clean "no description"
            # semantics, matching agent_types_api._coerce.
            if field == "description" and value == "":
                value = None
            setattr(row, field, value)
    db.session.commit()
    return jsonify(row.to_dict())


@workflows_bp.route("/workflows/<wf_id>", methods=["DELETE"])
def delete_workflow(wf_id):
    """Soft-delete (tombstone). Workflows have no built-ins, so there is
    no resurrect path to worry about."""
    row = db.session.get(Workflow, wf_id)
    if row is None:
        return jsonify({"error": "Workflow not found"}), 404
    row.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"status": "deleted"})
