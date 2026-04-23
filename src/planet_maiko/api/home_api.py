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
        items.append({
            "kind": "review",
            "task_id": t.id,
            "job_id": None,
            "title": t.title,
            "repo": extra.get("repo") or extra.get("repository"),
            "agent_name": _agent_name(t.assigned_agent_id),
            "route": f"/tasks/{t.id}/review",
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
        items.append({
            "kind": "plan",
            "task_id": task.id,
            "job_id": None,
            "title": task.title,
            "repo": task_extra.get("repo") or task_extra.get("repository"),
            "agent_name": _agent_name(task.assigned_agent_id),
            "route": f"/tasks/{task.id}/plan",
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "memo_id": m.id,
        })

    # 3. Standalone AgentJobs with an artifact — cartograph walks,
    #    investigation reports, etc. Scope to status=done + no linked
    #    Task (linked ones are covered by case 1). Cap at 10 days old
    #    so long-finished reports don't pile up; user can still find
    #    them via the Agents page.
    from datetime import timedelta
    cutoff = now - timedelta(days=10)
    jobs = (
        AgentJob.query
        .filter(AgentJob.status == "done")
        .filter(AgentJob.source_task_id.is_(None))
        .filter(AgentJob.artifact.isnot(None))
        .filter(AgentJob.finished_at >= cutoff)
        .order_by(AgentJob.finished_at.desc())
        .limit(20)
        .all()
    )
    for j in jobs:
        extra = j.extra or {}
        # Only surface once — the "seen" bit lives in extra.reviewed
        # and can be flipped by the frontend via POST /agent-jobs/<id>/ack.
        if extra.get("reviewed"):
            continue
        # Cartograph artifacts live on the Playbook tab; other kinds
        # land on the Pack page where the job row surfaces the report.
        # Once a dedicated artifact-viewer route exists, swap in a
        # /tasks/jobs/<id>-style deep link here.
        if j.kind == "cartograph":
            route = "/knowledge?tab=playbook"
        else:
            route = "/agents"
        items.append({
            "kind": "job_artifact",
            "task_id": None,
            "job_id": j.id,
            "title": j.title,
            "repo": j.scope_repo,
            "agent_name": _agent_name(j.agent_profile_id),
            "route": route,
            "age_seconds": _age(j.finished_at),
            "timestamp": iso_utc(j.finished_at),
        })

    # 4. job_approval Memos — "ask me first" automations that used to
    #    create a pending_approval AgentJob now create a Memo carrying
    #    the job spec in extra.job_spec. Approve mints the real
    #    AgentJob; dismiss just marks the memo done. No phantom jobs
    #    in the DB until the user actually commits.
    job_approval_memos = (
        Memo.query
        .filter(Memo.kind == "job_approval")
        .filter(Memo.status.in_(("pending", "seen")))
        .order_by(Memo.created_at.desc())
        .all()
    )
    for m in job_approval_memos:
        spec = (m.extra or {}).get("job_spec") or {}
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
        })

    # Legacy: AgentJobs already in the DB with status=pending_approval
    # from pre-memo-migration. Shown until the user approves/dismisses
    # them through their existing approve endpoints.
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
            "agent_name": _agent_name(j.agent_profile_id),
            "route": None,
            "age_seconds": _age(j.created_at),
            "timestamp": iso_utc(j.created_at),
            "job_kind": j.kind,
            "description": j.description,
        })

    # 5. Agent proposals — PROPOSAL:/TASK: blocks parsed out of
    #    investigation / review agent output. Now kind=agent_proposal
    #    Memos instead of pupdates. ProposalCard gets the full memo
    #    dict inline — its shape (title, body, extra.draft,
    #    extra.from_agent_id) mirrors the old pupdate dict close enough
    #    that the component handles both with small detection logic.
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
            "agent_name": m.source_agent_id,
            "route": None,
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "proposal": m.to_dict(),
            "memo_id": m.id,
        })

    # Legacy agent_proposal pupdates still in the DB (pre-Memo
    # migration). Shown until they age out / get dismissed. Newer
    # proposals come from memos above.
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
            "agent_name": extra.get("from_agent_id"),
            "route": None,
            "age_seconds": _age(p.timestamp),
            "timestamp": iso_utc(p.timestamp),
            "proposal": p.to_dict(),
        })

    # 6. Notifications — kind=notification Memos from the notify_me
    #    automation action. Info-category, dismissable, optional url.
    #    Used to live in their own NotificationsPane; folded into the
    #    unified Memos feed so there's one place to scan.
    notification_memos = (
        Memo.query
        .filter(Memo.kind == "notification")
        .filter(Memo.status.in_(("pending", "seen")))
        .order_by(Memo.created_at.desc())
        .limit(50)
        .all()
    )
    for m in notification_memos:
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
        })

    # 7. Agent-ready / agent-stuck Memos. These fire when a one-shot
    #    agent replies ready_for_review or stuck. They carry the task
    #    link so the CTA routes straight to the diff or the task page.
    #    Dedup against the #1 review-task entries — one memo per task
    #    is enough, and the task row is the source-of-truth record.
    agent_signal_memos = (
        Memo.query
        .filter(Memo.kind.in_(("agent_ready", "agent_stuck")))
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
        route = (m.extra or {}).get("review_url") or m.url
        items.append({
            "kind": m.kind,
            "task_id": m.source_task_id,
            "job_id": None,
            "title": m.title,
            "body": m.body,
            "repo": None,
            "agent_name": m.source_agent_id,
            "route": route,
            "cta_label": m.cta_label,
            "age_seconds": _age(m.created_at),
            "timestamp": iso_utc(m.created_at),
            "priority": m.priority,
            "memo_id": m.id,
        })

    # Fresh items first — the user wants to see "what just landed",
    # not "what's been sitting forever".
    items.sort(key=lambda x: x["age_seconds"])
    return jsonify({"items": items})


@home_bp.route("/home/shipped-today", methods=["GET"])
def get_shipped_today():
    """Tasks closed (done or cancelled) in the last 24 hours.

    Used by the Home sidebar's "Shipped today" widget — a light touch
    of "arc of the day" context instead of a raw counts grid. Cap at
    20 rows, newest first.
    """
    from datetime import datetime, timezone, timedelta
    from planet_maiko.models.task import Task

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (
        Task.query
        .filter(Task.status.in_(["done", "cancelled"]))
        .filter(Task.updated_at >= cutoff)
        .order_by(Task.updated_at.desc())
        .limit(20)
        .all()
    )
    return jsonify({
        "items": [
            {
                "id": t.id,
                "title": t.title,
                "type": t.type,
                "status": t.status,
                "repo": (t.extra or {}).get("repo") or "",
                "finished_at": iso_utc(t.updated_at),
            }
            for t in rows
        ],
    })


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
