from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate

pupdates_bp = Blueprint("pupdates", __name__)


@pupdates_bp.route("/pupdates", methods=["GET"])
def list_pupdates():
    """List active pupdates, with optional filtering and pagination."""
    source = request.args.get("source")
    priority = request.args.get("priority")
    category = request.args.get("category")  # "action" | "activity"
    show_dismissed = request.args.get("dismissed", "false").lower() == "true"
    limit = min(int(request.args.get("limit", 200)), 500)
    offset = int(request.args.get("offset", 0))

    query = Pupdate.query
    if not show_dismissed:
        query = query.filter_by(dismissed=False)
    if source:
        query = query.filter_by(source=source)
    if priority:
        query = query.filter_by(priority=priority)
    if category:
        query = query.filter_by(category=category)

    pupdates = query.order_by(Pupdate.timestamp.desc()).limit(limit).offset(offset).all()
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
    # Validate input lengths
    if len(data.get("title", "")) > 500:
        from flask import abort
        abort(400, "Title too long (max 500 chars)")
    if len(data.get("body", "") or "") > 10000:
        from flask import abort
        abort(400, "Body too long (max 10000 chars)")

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

    from planet_maiko.plugins.loader import fire_hook
    fire_hook("on_pupdate_created", pupdate)

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


@pupdates_bp.route("/proposals", methods=["POST"])
def create_proposal():
    """Create an agent_proposal pupdate — proposed follow-up work that lands
    in From Maiko for user approval.

    Body shape: {
      id?, title, from_agent_id, reasoning,
      draft: { title, type, priority, repo, category, description, depends_on? },
      priority?
    }
    The draft is exactly the shape approve_proposal() will use to create a Task.
    """
    import uuid as _uuid
    data = request.get_json(silent=True) or {}

    if not data.get("title") or not data.get("draft"):
        return jsonify({"error": "title and draft are required"}), 400

    pupdate_id = data.get("id") or f"proposal-{_uuid.uuid4().hex[:10]}"
    pupdate = Pupdate(
        id=pupdate_id,
        source="maiko",
        type="agent_proposal",
        priority=data.get("priority", "normal"),
        title=data["title"],
        body=data.get("reasoning") or "",
        actionable=True,
        action_hint="Approve / edit / dismiss",
        tags=["proposal", "from_maiko"],
        extra={
            "from_agent_id": data.get("from_agent_id"),
            "draft": data["draft"],
        },
    )
    db.session.add(pupdate)
    db.session.commit()
    return jsonify(pupdate.to_dict()), 201


@pupdates_bp.route("/proposals/<pupdate_id>/approve", methods=["POST"])
def approve_proposal(pupdate_id):
    """Turn an agent_proposal into a real routed task.

    The body can include an edited draft; otherwise we use whatever's in
    the pupdate's extra.draft. On success we mark the pupdate dismissed
    so it leaves the From Maiko queue — the resulting task is the new
    artifact to track.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route, is_ready
    import uuid as _uuid

    pupdate = db.get_or_404(Pupdate, pupdate_id)
    if pupdate.type != "agent_proposal":
        return jsonify({"error": "not a proposal"}), 400

    body = request.get_json(silent=True) or {}
    draft = body.get("draft") or (pupdate.extra or {}).get("draft") or {}
    if not draft.get("title"):
        return jsonify({"error": "draft.title is required"}), 400

    task = Task(
        id=f"task-{_uuid.uuid4().hex[:10]}",
        title=draft["title"],
        type=draft.get("type") or "todo",
        priority=draft.get("priority") or pupdate.priority or "normal",
        status="new",
        source_pupdate_id=pupdate.id,
        url=pupdate.url,
        extra={
            "description": draft.get("description") or pupdate.body or "",
            "repo": draft.get("repo") or "",
            "category": draft.get("category") or "",
            "from_proposal": pupdate.id,
        },
        tags=["from_proposal"],
        depends_on=draft.get("depends_on") or [],
    )
    db.session.add(task)
    db.session.flush()

    # Route (honoring any explicit override from the edited draft)
    override = draft.get("assigned_agent_id")
    if override:
        task.assigned_agent_id = override
    else:
        route(task)
    task.status = "blocked" if not is_ready(task) else "new"

    # Dismiss the proposal — it's been actioned.
    pupdate.dismissed = True
    pupdate.dismissed_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({"task": task.to_dict(), "proposal_id": pupdate.id}), 201


@pupdates_bp.route("/proposals/<pupdate_id>/dismiss", methods=["POST"])
def dismiss_proposal(pupdate_id):
    """Reject a proposal — dismisses the pupdate. Stored as feedback for
    the proposing agent (decremented score, tag on the pupdate)."""
    pupdate = db.get_or_404(Pupdate, pupdate_id)
    if pupdate.type != "agent_proposal":
        return jsonify({"error": "not a proposal"}), 400
    pupdate.dismissed = True
    pupdate.dismissed_at = datetime.now(timezone.utc)
    pupdate.tags = list(pupdate.tags or []) + ["rejected"]
    db.session.commit()
    return jsonify(pupdate.to_dict())
