"""Home overview HTTP surface.

Thin blueprint over `planet_maiko.brain.overview`. Both routes delegate
to that module and translate Python exceptions into JSON HTTP responses.

Contract:

    GET /api/home/overview
        Return the most recent overview, regenerating on the fly if the
        cache is stale (>4h) or missing. On first-ever hit with no
        cache, this is a blocking call (~1-2 min) — the frontend shows
        a warm loading state during that wait.

    POST /api/home/overview/refresh
        Force-regenerate and return the fresh result, regardless of
        cache age.

Both responses have the same shape:

    {
        "overview": { ... parsed JSON from the LLM ... },
        "generated_at": "<iso>",
        "stale_triggered_regen": bool
    }

Failure responses are `{"error": "<message>", "last_good": {...?}}` with
a non-200 status. When there's a prior successful overview in the DB
it's attached as `last_good` so the frontend can keep showing something
while the user investigates.
"""

import json
import logging

from flask import Blueprint, jsonify

from planet_maiko.brain.overview import (
    generate_overview,
    get_latest_overview,
    _read_cached_overview,
)
from planet_maiko.database import iso_utc

logger = logging.getLogger(__name__)

home_bp = Blueprint("home", __name__)


def _last_good_payload():
    """Best-effort last-known-good overview for error response bodies.

    Reads the file cache rather than trying to regenerate; returns
    None if nothing's cached.
    """
    generated_at, overview = _read_cached_overview()
    if overview is None:
        return None
    return {
        "overview": overview,
        "generated_at": generated_at,
    }


@home_bp.route("/home/overview", methods=["GET"])
def get_home_overview():
    """Return the current overview, regenerating if the cache is stale.

    Never raises; on LLM / parse failure returns a 500 with `error` and
    an optional `last_good` snapshot so the frontend can keep showing
    something.
    """
    try:
        result = get_latest_overview()
        return jsonify({
            "overview": result["overview"],
            "generated_at": result["generated_at"],
            "stale_triggered_regen": bool(result["stale"]),
        })
    except Exception as e:
        logger.exception("[home] overview generation failed: %s", e)
        body = {"error": str(e)}
        last_good = _last_good_payload()
        if last_good is not None:
            body["last_good"] = last_good
        return jsonify(body), 500


@home_bp.route("/home/review-queue", methods=["GET"])
def get_review_queue():
    """Everything waiting on the user's review — plans to approve,
    diffs to look at, pack-owned artifacts to read.

    The Home overview is LLM-curated and narrative; this endpoint is
    dumb-and-exhaustive so nothing slips past the 3-item truncation.

    Items are shaped:
        {
            kind:   "plan" | "review" | "job_artifact" | "proposal",
            task_id: <str|null>,      # null for standalone AgentJobs
            job_id:  <str|null>,      # set when driven by an AgentJob
            title:   <str>,
            repo:    <str|null>,
            agent_name: <str|null>,
            route:   <str>,           # where the UI should navigate
            age_seconds: <int>,
            timestamp: <iso>,
            proposal: <pupdate dict|undefined>,  # present for kind="proposal"
        }
    """
    from datetime import datetime, timezone
    from planet_maiko.database import db
    from planet_maiko.models.task import Task
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.models.agent_profile import AgentProfile

    now = datetime.now(timezone.utc)
    items = []

    def _agent_name(agent_id):
        if not agent_id:
            return None
        p = db.session.get(AgentProfile, agent_id)
        return p.display_name if p else None

    def _age(ts):
        if ts is None:
            return 0
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, int((now - ts).total_seconds()))

    # 1. Tasks in review — agent finished work, user needs to look at the diff.
    for t in Task.query.filter(Task.status == "review").all():
        extra = t.extra or {}
        job_id = extra.get("agent_job_id")
        if not job_id:
            continue
        items.append({
            "kind": "review",
            "task_id": t.id,
            "job_id": job_id,
            "title": t.title,
            "repo": extra.get("repo") or extra.get("repository"),
            "agent_id": t.assigned_agent_id,
            "agent_name": _agent_name(t.assigned_agent_id),
            "route": f"/jobs/{job_id}?view=diff",
            "age_seconds": _age(t.updated_at),
            "timestamp": iso_utc(t.updated_at),
        })

    # 2. Agent plan memos (waiting on user's nod). Source is Memo now
    #    (kind=agent_plan) — pupdates of type agent_plan_for_approval
    #    are retired. Dedup by source_task_id so multiple plan revisions
    #    from the same agent don't double-list.
    from planet_maiko.models.memo import Memo
    plan_memos = (
        Memo.query
        .filter(Memo.kind == "agent_plan")
        .filter(Memo.status.in_(("pending", "seen")))
        .order_by(Memo.created_at.desc())
        .all()
    )
    seen_task_ids = set()
    for m in plan_memos:
        task_id = m.source_task_id
        if not task_id or task_id in seen_task_ids:
            continue
        seen_task_ids.add(task_id)
        task = db.session.get(Task, task_id)
        if task is None or task.status in ("done", "cancelled"):
            continue
        task_extra = task.extra or {}
        job_id = task_extra.get("agent_job_id")
        if not job_id:
            continue
        items.append({
            "kind": "plan",
            "task_id": task.id,
            "job_id": job_id,
            "title": task.title,
            "repo": task_extra.get("repo") or task_extra.get("repository"),
            "agent_id": task.assigned_agent_id,
            "agent_name": _agent_name(task.assigned_agent_id),
            "route": f"/jobs/{job_id}?view=plan",
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "memo_id": m.id,
        })

    # 3. job_approval Memos. "Ask me first" automations create a Memo
    #    carrying the job spec in extra.job_spec. Approve mints the
    #    real AgentJob; dismiss just marks the memo done. No phantom
    #    jobs in the DB until the user actually commits.
    job_approval_memos = (
        Memo.query
        .filter(Memo.kind == "job_approval")
        .filter(Memo.status.in_(("pending", "seen")))
        .order_by(Memo.created_at.desc())
        .all()
    )
    for m in job_approval_memos:
        extra = m.extra or {}
        spec = extra.get("job_spec") or {}
        items.append({
            "kind": "pending_job",
            "task_id": None,
            "job_id": None,
            "memo_id": m.id,
            "title": m.title,
            "repo": spec.get("scope_repo"),
            "agent_name": None,
            "route": None,
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "job_kind": spec.get("kind"),
            "description": m.body or spec.get("description"),
            "pupdate_snapshot": extra.get("pupdate_snapshot"),
        })

    # AgentJobs already in the DB with status=pending_approval. Shown
    # until the user approves/dismisses them.
    pending_jobs = (
        AgentJob.query
        .filter(AgentJob.status == "pending_approval")
        .order_by(AgentJob.created_at.desc())
        .all()
    )
    for j in pending_jobs:
        items.append({
            "kind": "pending_job",
            "task_id": None,
            "job_id": j.id,
            "memo_id": None,
            "title": j.title,
            "repo": j.scope_repo,
            "agent_id": j.agent_profile_id,
            "agent_name": _agent_name(j.agent_profile_id),
            "route": None,
            "age_seconds": _age(j.created_at),
            "timestamp": iso_utc(j.created_at),
            "job_kind": j.kind,
            "description": j.description,
        })

    # 4. Agent proposals. PROPOSAL:/TASK: blocks parsed out of
    #    investigation / review agent output, surfaced as
    #    kind=agent_proposal Memos. ProposalCard gets the full memo
    #    dict inline; its shape (title, body, extra.draft,
    #    extra.from_agent_id) is shared with the pupdate fallback below
    #    so one component handles both with small detection logic.
    proposal_memos = (
        Memo.query
        .filter(Memo.kind == "agent_proposal")
        .filter(Memo.status.in_(("pending", "seen")))
        .order_by(Memo.created_at.desc())
        .limit(30)
        .all()
    )
    for m in proposal_memos:
        items.append({
            "kind": "proposal",
            "task_id": m.source_task_id,
            "job_id": None,
            "title": m.title,
            "repo": (m.extra or {}).get("draft", {}).get("repo"),
            "agent_id": m.source_agent_id,
            "agent_name": _agent_name(m.source_agent_id),
            "route": None,
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "proposal": m.to_dict(),
            "memo_id": m.id,
        })

    # agent_proposal pupdates still in the DB. Shown until they age
    # out or get dismissed. New proposals come from memos above.
    proposals = (
        Pupdate.query
        .filter(Pupdate.type == "agent_proposal")
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .order_by(Pupdate.timestamp.desc())
        .limit(30)
        .all()
    )
    for p in proposals:
        extra = p.extra or {}
        draft = extra.get("draft") or {}
        items.append({
            "kind": "proposal",
            "task_id": None,
            "job_id": None,
            "title": p.title,
            "repo": draft.get("repo") or None,
            "agent_id": extra.get("from_agent_id"),
            "agent_name": _agent_name(extra.get("from_agent_id")),
            "route": None,
            "age_seconds": _age(p.timestamp),
            "timestamp": iso_utc(p.timestamp),
            "proposal": p.to_dict(),
        })

    # 5. Notifications. kind=notification Memos from the notify_me
    #    automation action. Info-category, dismissable, optional url.
    notification_memos = (
        Memo.query
        .filter(Memo.kind == "notification")
        .filter(Memo.status.in_(("pending", "seen")))
        .order_by(Memo.created_at.desc())
        .limit(50)
        .all()
    )
    for m in notification_memos:
        extra = m.extra or {}
        items.append({
            "kind": "notification",
            "task_id": None,
            "job_id": None,
            "title": m.title,
            "body": m.body,
            "repo": None,
            "agent_name": None,
            "route": m.url,
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "priority": m.priority,
            "memo_id": m.id,
            # Snapshot of the triggering pupdate so the frontend can
            # render a "triggered by" context card below the memo body.
            # Only set when the memo came from a pupdate-scoped
            # automation action; standalone notifications leave it null.
            "pupdate_snapshot": extra.get("pupdate_snapshot"),
        })

    # 6. Agent-ready Memos. These fire when a one-shot agent replies
    #    ready_for_review. They carry the task link so the CTA routes
    #    straight to the diff. Dedup against the review-task entries
    #    above so each task surfaces once, with the task row as the
    #    source-of-truth record.
    #
    #    agent_stuck is handled by the persistent pack dock (unread
    #    badge + click-through to chat), not this pane, to avoid two
    #    surfaces showing the same thing.
    agent_signal_memos = (
        Memo.query
        .filter(Memo.kind == "agent_ready")
        .filter(Memo.status.in_(("pending", "seen")))
        .order_by(Memo.created_at.desc())
        .limit(50)
        .all()
    )
    already_linked_tasks = {i.get("task_id") for i in items if i.get("task_id")}
    for m in agent_signal_memos:
        if m.source_task_id and m.source_task_id in already_linked_tasks:
            # The review-task entry (#1) or plan entry (#2) already
            # represents this user-owed work; a second row would just
            # clutter the pane.
            continue
        # Memos carry the canonical /jobs/<id>?view=chat URL set at
        # emit time. For agent_stuck memos with no url we fall back to
        # /agents so the user always lands somewhere.
        route = m.url or (m.extra or {}).get("review_url")
        if m.kind == "agent_stuck" and not route:
            route = "/agents"
        items.append({
            "kind": m.kind,
            "task_id": m.source_task_id,
            "job_id": None,
            "title": m.title,
            "body": m.body,
            "repo": None,
            "agent_id": m.source_agent_id,
            "agent_name": _agent_name(m.source_agent_id),
            "route": route,
            "cta_label": m.cta_label,
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "priority": m.priority,
            "memo_id": m.id,
        })

    # 7. skill_result Memos. Output from skills the user (or
    #    automations) ran. Skips kinds that self-render elsewhere
    #    (home-overview, scene, scene-mood) since they have their own
    #    surfaces and would just be noise here.
    skill_result_memos = (
        Memo.query
        .filter(Memo.kind == "skill_result")
        .filter(Memo.status.in_(("pending", "seen")))
        .order_by(Memo.created_at.desc())
        .limit(50)
        .all()
    )
    SELF_RENDERING_SKILLS = {"home-overview", "scene", "scene-mood"}
    for m in skill_result_memos:
        skill_name = (m.extra or {}).get("skill_name")
        if skill_name in SELF_RENDERING_SKILLS:
            continue
        from_job = (m.extra or {}).get("from_agent_job")
        # Skill runs that came from an AgentJob get a deep-link to
        # the unified /jobs/<id> viewer (full-page render + chat).
        # Standalone skill runs (no job) keep route=None — the inline
        # body expand on the Memos pane is the only surface, which is
        # fine for short outputs.
        items.append({
            "kind": "skill_result",
            "task_id": None,
            "job_id": from_job,
            "memo_id": m.id,
            "title": m.title,
            "body": m.body,
            "body_truncated": bool(m.body and len(m.body) > 8000),
            "repo": None,
            "agent_id": m.source_agent_id,
            "agent_name": _agent_name(m.source_agent_id),
            "route": f"/jobs/{from_job}" if from_job else None,
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "priority": m.priority,
            "skill_name": skill_name,
            "kind_label": (m.extra or {}).get("skill_title") or skill_name,
        })

    # 9. Catchall — every other pending/seen Memo gets surfaced with
    #    a generic shape so plugin-introduced kinds show up on the
    #    home pane without needing this endpoint to grow another
    #    section. Only appends memos NOT already represented above
    #    (dedup by memo_id) so the kind-specific shaping wins for
    #    rich kinds like agent_proposal / job_approval / agent_ready.
    #
    #    Skips agent_message and agent_stuck: the persistent pack dock
    #    surfaces both live with an unread badge and click-to-chat, so
    #    duplicating them here just gives the user two places to
    #    dismiss the same conversation.
    seen_memo_ids = {i.get("memo_id") for i in items if i.get("memo_id") is not None}
    SUPPRESSED_KINDS_IN_CATCHALL = {"agent_message", "agent_stuck"}
    other_memos = (
        Memo.query
        .filter(Memo.status.in_(("pending", "seen")))
        .filter(~Memo.kind.in_(SUPPRESSED_KINDS_IN_CATCHALL))
        .order_by(Memo.created_at.desc())
        .limit(200)
        .all()
    )
    for m in other_memos:
        if m.id in seen_memo_ids:
            continue
        extra = m.extra or {}
        # Route heuristic: if the memo carries a task or job link,
        # send the user there. Otherwise fall back to /agents.
        route = m.url
        if not route and m.source_task_id:
            # source_task_id may be either a Task id or an AgentJob
            # id; both render under /agents via the active-agents
            # chat thread.
            route = "/agents"
        items.append({
            "kind": m.kind,
            "task_id": m.source_task_id,
            "job_id": None,
            "memo_id": m.id,
            "title": m.title,
            "body": m.body,
            "repo": None,
            "agent_id": m.source_agent_id,
            "agent_name": _agent_name(m.source_agent_id),
            "route": route,
            "cta_label": m.cta_label,
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "priority": m.priority,
            # Pass thread_id when the source points at an agent run
            # (Task or AgentJob id) so the frontend can deep-link
            # into the chat modal directly.
            "thread_id": extra.get("job_id") or extra.get("task_id") or m.source_task_id,
        })

    # Fresh items first — the user wants to see "what just landed",
    # not "what's been sitting forever".
    items.sort(key=lambda x: x["age_seconds"])
    return jsonify({"items": items})


@home_bp.route("/home/overview/refresh", methods=["POST"])
def refresh_home_overview():
    """Force-regenerate the overview, bypassing the cache age check."""
    try:
        parsed = generate_overview()
        generated_at, _ = _read_cached_overview()
        return jsonify({
            "overview": parsed,
            "generated_at": generated_at,
            "stale_triggered_regen": True,
        })
    except Exception as e:
        logger.exception("[home] overview refresh failed: %s", e)
        body = {"error": str(e)}
        last_good = _last_good_payload()
        if last_good is not None:
            body["last_good"] = last_good
        return jsonify(body), 500
