from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate

pupdates_bp = Blueprint("pupdates", __name__)


@pupdates_bp.route("/pupdates", methods=["GET"])
def list_pupdates():
    """List active pupdates, with optional filtering."""
    source = request.args.get("source")
    priority = request.args.get("priority")
    show_dismissed = request.args.get("dismissed", "false").lower() == "true"

    query = Pupdate.query
    if not show_dismissed:
        query = query.filter_by(dismissed=False)
    if source:
        query = query.filter_by(source=source)
    if priority:
        query = query.filter_by(priority=priority)

    pupdates = query.order_by(Pupdate.timestamp.desc()).all()
    return jsonify([p.to_dict() for p in pupdates])


@pupdates_bp.route("/pupdates/<pupdate_id>", methods=["GET"])
def get_pupdate(pupdate_id):
    """Get a single pupdate by ID."""
    pupdate = db.get_or_404(Pupdate, pupdate_id)
    return jsonify(pupdate.to_dict())


@pupdates_bp.route("/pupdates", methods=["POST"])
def create_pupdate():
    """Create a new pupdate."""
    data = request.get_json()
    pupdate = Pupdate(
        id=data["id"],
        source=data["source"],
        source_id=data.get("source_id"),
        type=data["type"],
        priority=data.get("priority", "normal"),
        title=data["title"],
        body=data.get("body"),
        url=data.get("url"),
        actionable=data.get("actionable", False),
        action_hint=data.get("action_hint"),
        tags=data.get("tags", []),
        extra=data.get("metadata", {}),
    )
    if data.get("expires_at"):
        pupdate.expires_at = datetime.fromisoformat(data["expires_at"])

    db.session.add(pupdate)
    db.session.commit()
    return jsonify(pupdate.to_dict()), 201


@pupdates_bp.route("/pupdates/<pupdate_id>/read", methods=["POST"])
def mark_read(pupdate_id):
    """Mark a pupdate as read."""
    pupdate = db.get_or_404(Pupdate, pupdate_id)
    pupdate.read = True
    db.session.commit()
    return jsonify(pupdate.to_dict())


@pupdates_bp.route("/pupdates/<pupdate_id>/dismiss", methods=["POST"])
def dismiss_pupdate(pupdate_id):
    """Dismiss (archive) a pupdate."""
    pupdate = db.get_or_404(Pupdate, pupdate_id)
    pupdate.dismissed = True
    pupdate.dismissed_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(pupdate.to_dict())
