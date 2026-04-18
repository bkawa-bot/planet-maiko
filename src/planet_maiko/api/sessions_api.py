"""Sessions API — external-orchestrator surface for A2A awareness.

Phase A of the external-orchestrator plan. Any tool running LLM coding
sessions outside Planet Maiko registers its session here so the
awareness phase can include its worktree in the conflict scan, and
can query conflicts on demand without waiting for the 5-minute cycle.

Endpoints:
    POST /api/sessions/register              — register a new session
    POST /api/sessions/<session_id>/complete — mark a session done (idempotent)
    GET  /api/sessions/<session_id>/conflicts — on-demand conflict scan

No auth, no rate limiting, no multi-tenant separation — Phase A is
local-brain only. Read surface, compliance, and producer hooks are
later phases.
"""

import uuid as _uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from planet_maiko.database import db, iso_utc
from planet_maiko.models.external_session import ExternalSession

sessions_bp = Blueprint("sessions", __name__)


def _active_external_worktrees():
    """Worktree dicts for every currently-active external session.

    Shape matches what coding_agent.list_prepared() produces for
    Maiko-prepared worktrees ({"task_id", "worktree_path"}) so the
    awareness detector doesn't have to care which orchestrator owns
    a given session.
    """
    rows = ExternalSession.query.filter(
        ExternalSession.status == "active",
        ExternalSession.completed_at.is_(None),
    ).all()
    return [
        {"task_id": r.session_id, "worktree_path": r.worktree_path}
        for r in rows
    ]


def _prepared_worktrees():
    """Worktree dicts for every Maiko-prepared active agent.

    Mirrors the list built in brain/cycle.py `_phase_awareness`. Kept
    here so the /conflicts endpoint sees the same population the cycle
    phase does.
    """
    from planet_maiko.agents.coding_agent import list_prepared
    prepared = list_prepared()
    return [
        {"task_id": a.get("task_id", ""), "worktree_path": a.get("working_path", "")}
        for a in prepared if a.get("working_path")
    ]


@sessions_bp.route("/sessions", methods=["GET"])
def list_sessions():
    """List external sessions. Filter via ?status= (default: active).

    Statuses: "active" | "completed" | "all". Results are capped at
    200 and ordered by registered_at descending so the freshest
    registrations land first — matches what the Agents page wants
    when it surfaces the strip alongside Maiko-prepared agents.
    """
    status = request.args.get("status", "active")
    q = ExternalSession.query
    if status != "all":
        q = q.filter(ExternalSession.status == status)
    rows = q.order_by(ExternalSession.registered_at.desc()).limit(200).all()
    return jsonify([r.to_dict() for r in rows])


@sessions_bp.route("/sessions/register", methods=["POST"])
def register_session():
    """Register a new external session.

    Body: {repo, worktree_path, session_id?, consumer?, hint?}.
    Returns 201 with {session_id, registered_at}.
    400 if repo or worktree_path is missing.
    409 if a caller-supplied session_id already exists.
    """
    data = request.get_json() or {}
    repo = (data.get("repo") or "").strip()
    worktree_path = (data.get("worktree_path") or "").strip()
    if not repo:
        return jsonify({"error": "repo is required"}), 400
    if not worktree_path:
        return jsonify({"error": "worktree_path is required"}), 400

    supplied_id = (data.get("session_id") or "").strip() or None
    if supplied_id:
        existing = ExternalSession.query.filter_by(session_id=supplied_id).first()
        if existing is not None:
            return jsonify({
                "error": "session_id already exists",
                "session_id": supplied_id,
            }), 409
        session_id = supplied_id
    else:
        session_id = _uuid.uuid4().hex

    session = ExternalSession(
        session_id=session_id,
        consumer=(data.get("consumer") or None),
        repo=repo,
        worktree_path=worktree_path,
        hint=(data.get("hint") or None),
    )
    db.session.add(session)
    db.session.commit()

    return jsonify({
        "session_id": session.session_id,
        "registered_at": iso_utc(session.registered_at),
    }), 201


@sessions_bp.route("/sessions/<session_id>/complete", methods=["POST"])
def complete_session(session_id):
    """Mark a session completed. Body: {outcome?} — outcome merged into extra.

    Idempotent: calling on an already-completed session returns 200
    with the existing state (no 409). Phase A callers may retry on
    network flakes and we don't want that to be an error.
    """
    session = ExternalSession.query.filter_by(session_id=session_id).first()
    if session is None:
        return jsonify({"error": "session not found", "session_id": session_id}), 404

    if session.status == "completed":
        return jsonify({
            "session_id": session.session_id,
            "status": session.status,
            "completed_at": iso_utc(session.completed_at),
        })

    data = request.get_json(silent=True) or {}
    outcome = data.get("outcome")
    if outcome is not None:
        extra = dict(session.extra or {})
        extra["outcome"] = outcome
        session.extra = extra

    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({
        "session_id": session.session_id,
        "status": session.status,
        "completed_at": iso_utc(session.completed_at),
    })


@sessions_bp.route("/sessions/<session_id>/conflicts", methods=["GET"])
def session_conflicts(session_id):
    """On-demand conflict scan for one session.

    Returns only conflicts involving this session's worktree — every
    other active session (external + Maiko-prepared) is evaluated as
    a potential peer, and the result is filtered to edges the focus
    worktree participates in.
    """
    from planet_maiko.brain.awareness.conflicts import detect_conflicts

    session = ExternalSession.query.filter_by(session_id=session_id).first()
    if session is None:
        return jsonify({"error": "session not found", "session_id": session_id}), 404

    worktrees = _prepared_worktrees() + _active_external_worktrees()

    conflicts = detect_conflicts(
        worktrees,
        focus_worktree_path=session.worktree_path,
    )

    return jsonify({
        "session_id": session.session_id,
        "conflicts": conflicts,
    })
