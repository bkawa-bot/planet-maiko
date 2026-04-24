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
    # brain_processed filter — used by the Brain Queue tab to show
    # exactly the pupdates the brain cycle hasn't routed yet.
    # Pass "false" for the unprocessed queue, "true" for processed,
    # omit for "don't care".
    brain_processed_raw = request.args.get("brain_processed")
    brain_processed = None
    if brain_processed_raw is not None:
        brain_processed = brain_processed_raw.lower() in ("true", "1", "yes")

    query = Pupdate.query
    if not show_dismissed:
        query = query.filter_by(dismissed=False)
    if source:
        query = query.filter_by(source=source)
    if priority:
        query = query.filter_by(priority=priority)
    if category:
        query = query.filter_by(category=category)
    if brain_processed is not None:
        query = query.filter_by(brain_processed=brain_processed)

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


@pupdates_bp.route("/proposals/<pupdate_id>/approve-as-goal", methods=["POST"])
def approve_proposal_as_goal(pupdate_id):
    """Adopt a proposal as a standing Automation row.

    Gap-detector proposals carry a `proposed_goal` blob in extra with
    the legacy (kind, scope_repo, trigger_kind, trigger_config,
    action_kind, action_config) shape. We translate those known kinds
    into the new when[] + then[] schema and install an Automation.

    Endpoint name kept as approve-as-goal for backward compat with the
    ProposalCard frontend; the underlying object is an Automation now.
    """
    from planet_maiko.models.automation import Automation

    pupdate = db.get_or_404(Pupdate, pupdate_id)
    if pupdate.type != "agent_proposal":
        return jsonify({"error": "not a proposal"}), 400

    spec = (pupdate.extra or {}).get("proposed_goal")
    if not spec or not spec.get("kind"):
        return jsonify({"error": "proposal has no proposed_goal"}), 400

    # Translate the legacy (kind, trigger_config) shape into a when[]
    # entry. Only known kinds are supported; unknown kinds get an empty
    # `when` so they never fire, which is a soft-fail rather than a hard
    # refusal.
    kind = spec["kind"]
    scope_repo = spec.get("scope_repo")
    trigger_config = spec.get("trigger_config") or {}
    when = []
    then = []
    name = spec.get("name") or f"Imported proposal: {kind}"
    description = (spec.get("extra") or {}).get("description") or ""

    if kind == "keep_overview_current":
        when = [{
            "kind": "overview_stale",
            "config": {
                "repo": scope_repo,
                "stale_days": int(trigger_config.get("stale_days", 30)),
            },
        }]
        then = [{
            "kind": "propose",
            "config": {
                "draft": {
                    "title": f"Cartograph {scope_repo}",
                    "type": "cartograph",
                    "priority": "normal",
                    "repo": scope_repo,
                    "description": f"Refresh Atlas's overview of {scope_repo}.",
                },
            },
        }]
        name = f"Keep {scope_repo}'s overview current"
    elif kind == "train_lora_when_ready":
        when = [{
            "kind": "lora_missing",
            "config": {
                "repo": scope_repo,
                "min_learnings": int(trigger_config.get("min_learnings", 10)),
            },
        }]
        then = [{
            "kind": "nudge",
            "config": {
                "title": f"Ready to train a LoRA for {scope_repo}?",
                "body": (
                    f"{scope_repo} has enough active rules and no adapter yet."
                ),
                "url": "/knowledge?tab=training",
                "action_hint": "Open Training",
            },
        }]
        name = f"Nudge when {scope_repo} is ready to train"

    # Dedup: if an active Automation already watches this (scope_repo +
    # first-condition-kind) pair, dismiss the proposal and return it.
    cond_kind = when[0]["kind"] if when else None
    if cond_kind:
        dupe = None
        for row in Automation.query.filter(
            Automation.scope_repo == scope_repo,
            Automation.status != "archived",
        ).all():
            if any(t.get("kind") == cond_kind for t in (row.when or [])):
                dupe = row
                break
        if dupe is not None:
            pupdate.dismissed = True
            pupdate.dismissed_at = datetime.now(timezone.utc)
            db.session.commit()
            return jsonify({"automation": dupe.to_dict(), "proposal_id": pupdate.id, "note": "already_installed"}), 200

    automation = Automation(
        name=name,
        description=description,
        when=when,
        when_logic="all",
        then=then,
        status="active",
        created_by="proposal",
        scope_repo=scope_repo,
        cooldown_days=7,
    )
    db.session.add(automation)

    pupdate.dismissed = True
    pupdate.dismissed_at = datetime.now(timezone.utc)

    db.session.commit()
    return jsonify({"automation": automation.to_dict(), "proposal_id": pupdate.id}), 201


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
