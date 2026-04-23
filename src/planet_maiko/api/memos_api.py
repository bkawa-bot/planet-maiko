"""Memos API — list / get / mark-seen / approve / dismiss / PATCH.

Memos are the canonical user-facing-state surface (distinct from
Pupdate which is a queue event, and Task which is concrete work).
See models/memo.py for the architectural rationale; this file is the
HTTP layer.
"""

import logging

from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.memo import Memo, VALID_CATEGORIES, VALID_STATUSES
from planet_maiko.brain import memos as memo_svc

logger = logging.getLogger(__name__)

memos_bp = Blueprint("memos", __name__)


@memos_bp.route("/memos", methods=["GET"])
def list_memos():
    """List memos with optional filters.

    Query params:
        kind:       single kind (e.g. skill_result, notification)
        category:   info | waiting | offer
        status:     pending | seen | actioned | dismissed  (repeatable)
        priority:   low | normal | high | urgent
        source_agent_id, source_task_id
        limit:      default 100, max 500
        offset:     default 0

    Default status filter: pending + seen (live memos). Pass explicit
    ?status=dismissed or ?status=actioned to see archive rows.
    """
    q = Memo.query

    kind = request.args.get("kind")
    if kind:
        q = q.filter(Memo.kind == kind)

    category = request.args.get("category")
    if category:
        if category not in VALID_CATEGORIES:
            return jsonify({"error": f"invalid category {category!r}"}), 400
        q = q.filter(Memo.category == category)

    statuses = request.args.getlist("status")
    if statuses:
        invalid = [s for s in statuses if s not in VALID_STATUSES]
        if invalid:
            return jsonify({"error": f"invalid status: {invalid}"}), 400
        q = q.filter(Memo.status.in_(statuses))
    else:
        # Default: live memos only. Archive is explicit opt-in.
        q = q.filter(Memo.status.in_(("pending", "seen")))

    priority = request.args.get("priority")
    if priority:
        q = q.filter(Memo.priority == priority)

    source_agent_id = request.args.get("source_agent_id")
    if source_agent_id:
        q = q.filter(Memo.source_agent_id == source_agent_id)

    source_task_id = request.args.get("source_task_id")
    if source_task_id:
        q = q.filter(Memo.source_task_id == source_task_id)

    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))

    rows = (
        q.order_by(Memo.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return jsonify([m.to_dict() for m in rows])


@memos_bp.route("/memos/<int:memo_id>", methods=["GET"])
def get_memo(memo_id):
    memo = db.get_or_404(Memo, memo_id)
    return jsonify(memo.to_dict())


@memos_bp.route("/memos/<int:memo_id>/mark-seen", methods=["POST"])
def mark_seen_route(memo_id):
    memo = db.get_or_404(Memo, memo_id)
    memo_svc.mark_seen(memo)
    db.session.commit()
    return jsonify(memo.to_dict())


@memos_bp.route("/memos/<int:memo_id>/dismiss", methods=["POST"])
def dismiss_route(memo_id):
    memo = db.get_or_404(Memo, memo_id)
    memo_svc.mark_dismissed(memo)
    db.session.commit()
    return jsonify(memo.to_dict())


@memos_bp.route("/memos/<int:memo_id>/approve", methods=["POST"])
def approve_route(memo_id):
    """Approve a memo — runs the kind-specific handler + marks actioned.

    Handlers are registered via brain.memos.register_approve_handler
    by the producing code (e.g. job_approval registers a handler that
    mints the actual AgentJob when the memo is approved).

    Memos without a registered handler return 400 — they're info-only
    and shouldn't have an approve button in the first place.
    """
    memo = db.get_or_404(Memo, memo_id)
    handler = memo_svc.APPROVE_HANDLERS.get(memo.kind)
    if not handler:
        return jsonify({
            "error": f"no approve handler registered for kind {memo.kind!r}",
        }), 400
    try:
        result = handler(memo) or {}
    except Exception as e:
        logger.exception(f"[memos] approve handler for {memo.kind} crashed")
        return jsonify({"error": f"approve failed: {e}"}), 500
    memo_svc.mark_actioned(memo)
    db.session.commit()
    return jsonify({"memo": memo.to_dict(), "result": result})


@memos_bp.route("/memos/<int:memo_id>", methods=["PATCH"])
def patch_memo(memo_id):
    """Edit a memo's user-facing fields. Useful for proposal-shaped
    memos where the user wants to tweak the draft before approving —
    e.g. rewriting the title or body of a TASK proposal.

    Allowed fields: title, body, url, cta_label, priority, extra.
    Status + timestamps + provenance fields are read-only; use the
    dedicated transition endpoints for those.
    """
    memo = db.get_or_404(Memo, memo_id)
    data = request.get_json(silent=True) or {}

    if "title" in data:
        memo.title = (data["title"] or "")[:300]
    if "body" in data:
        memo.body = data["body"]
    if "url" in data:
        memo.url = data["url"] or None
    if "cta_label" in data:
        memo.cta_label = data["cta_label"] or None
    if "priority" in data:
        memo.priority = data["priority"] or "normal"
    if "extra" in data:
        # Shallow replace — callers that want to merge do so client-side.
        memo.extra = data["extra"] or {}

    db.session.commit()
    return jsonify(memo.to_dict())
