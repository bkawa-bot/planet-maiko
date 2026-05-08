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

    Optional JSON body is forwarded to the handler so handlers can
    accept additional input (e.g. job_approval takes `repo_path` to
    override the local-clone lookup when the user picks one from
    the prompt).

    A handler that needs more input from the user raises
    MemoApproveNeedsInput; the endpoint converts that to a 422 with
    the payload and DOES NOT mark the memo actioned, so the frontend
    can show a picker and retry.

    Memos without a registered handler return 400 — they're info-only
    and shouldn't have an approve button in the first place.
    """
    from planet_maiko.brain.memo_handlers import MemoApproveNeedsInput

    memo = db.get_or_404(Memo, memo_id)
    handler = memo_svc.APPROVE_HANDLERS.get(memo.kind)
    if not handler:
        return jsonify({
            "error": f"no approve handler registered for kind {memo.kind!r}",
        }), 400
    data = request.get_json(silent=True) or {}
    try:
        result = handler(memo, data) or {}
    except MemoApproveNeedsInput as needs:
        # Don't mark actioned — the user needs to provide more input
        # before we commit. The frontend prompts and retries.
        return jsonify({
            "needs_input": needs.kind,
            "payload": needs.payload,
        }), 422
    except Exception as e:
        logger.exception(f"[memos] approve handler for {memo.kind} crashed")
        return jsonify({"error": f"approve failed: {e}"}), 500
    memo_svc.mark_actioned(memo)
    db.session.commit()
    return jsonify({"memo": memo.to_dict(), "result": result})


@memos_bp.route("/memos/<int:memo_id>/create-task", methods=["POST"])
def create_task_from_memo(memo_id):
    """Mint a Task from a memo (typically a notification) using its
    pupdate_snapshot for context. Marks the memo actioned.

    Body (all optional):
        title:    str — defaults to memo.title
        type:     str — task type ("todo", "bug", "feature", etc.).
                  Defaults to "todo".
        priority: str — "low" | "normal" | "high" | "urgent".
                  Defaults to memo.priority or "normal".
        description: str — defaults to memo.body or snapshot.body
        repo:     str — "org/name". Defaults to snapshot.extra.repo
                  if present.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route, is_ready
    import uuid as _uuid

    memo = db.get_or_404(Memo, memo_id)
    data = request.get_json(silent=True) or {}
    snapshot = (memo.extra or {}).get("pupdate_snapshot") or {}
    snap_extra = snapshot.get("extra") or {}

    title = (data.get("title") or memo.title or snapshot.get("title") or "Follow-up")[:300]
    task_type = (data.get("type") or "todo").strip() or "todo"
    priority = (data.get("priority") or memo.priority or "normal")
    description = (
        data.get("description")
        or memo.body
        or snapshot.get("body")
        or ""
    )
    repo = data.get("repo") or snap_extra.get("repo") or ""
    url = memo.url or snapshot.get("url") or None

    task_id = f"task-{_uuid.uuid4().hex[:10]}"
    task = Task(
        id=task_id,
        title=title,
        type=task_type,
        priority=priority,
        status="new",
        url=url,
        tags=["from_memo"],
        extra={
            "description": description,
            "repo": repo,
            "from_memo": memo.id,
            # Carry the snapshot so downstream surfaces (Assign modal,
            # build_task_prompt) can render the original context.
            "pupdate_snapshot": snapshot or None,
        },
    )
    db.session.add(task)
    db.session.flush()
    route(task)
    if not is_ready(task):
        task.status = "blocked"

    memo_svc.mark_actioned(memo)
    db.session.commit()
    return jsonify({"memo": memo.to_dict(), "task": task.to_dict()})


@memos_bp.route("/memos/<int:memo_id>/launch-agent", methods=["POST"])
def launch_agent_from_memo(memo_id):
    """Mint an AgentJob from a memo using its pupdate_snapshot for
    context. The job goes through the normal cycle execute phase —
    same path as automation-fired jobs. Marks the memo actioned.

    Body (all optional):
        kind:     str — job kind. Defaults to "investigation". Accepts
                  any registered specialty / one-shot role.
        title:    str — defaults to memo.title
        priority: str — defaults to memo.priority or "normal"
        scope_repo: str — "org/name". Defaults to snapshot.extra.repo.
    """
    from planet_maiko.models.agent_job import AgentJob
    from datetime import datetime, timezone
    import uuid as _uuid

    memo = db.get_or_404(Memo, memo_id)
    data = request.get_json(silent=True) or {}
    snapshot = (memo.extra or {}).get("pupdate_snapshot") or {}
    snap_extra = snapshot.get("extra") or {}

    kind = (data.get("kind") or "investigation").strip() or "investigation"
    title = (data.get("title") or memo.title or "Investigate")[:300]
    priority = data.get("priority") or memo.priority or "normal"
    scope_repo = data.get("scope_repo") or snap_extra.get("repo") or None
    description = memo.body or snapshot.get("body") or ""

    extra = {"from_memo": memo.id}
    if snapshot:
        extra["pupdate_snapshot"] = snapshot

    job_id = f"job-{_uuid.uuid4().hex[:10]}"
    job = AgentJob(
        id=job_id,
        kind=kind,
        title=title,
        description=description,
        scope_repo=scope_repo,
        priority=priority,
        created_by="user",
        requires_approval=False,
        status="queued",
        approved_by="user",
        approved_at=datetime.now(timezone.utc),
        extra=extra,
    )
    db.session.add(job)
    memo_svc.mark_actioned(memo)
    db.session.commit()
    return jsonify({"memo": memo.to_dict(), "job_id": job.id, "kind": kind})


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
