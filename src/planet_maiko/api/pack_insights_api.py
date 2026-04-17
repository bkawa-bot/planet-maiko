from datetime import datetime

from flask import Blueprint, jsonify, request
from planet_maiko.brain.learning.pack_insights import (
    get_state, start_gathering, collect_from_agents,
    add_manual_learning, synthesize, finalize, reset,
)
from planet_maiko.database import iso_utc

pack_insights_bp = Blueprint("pack_insights", __name__)


@pack_insights_bp.route("/pack-insights", methods=["GET"])
def pack_insights_state():
    """Get current Pack Insights gathering state."""
    return jsonify(get_state())


@pack_insights_bp.route("/pack-insights/start", methods=["POST"])
def pack_insights_start():
    """Start Pack Insights gathering."""
    return jsonify(start_gathering())


@pack_insights_bp.route("/pack-insights/collect", methods=["POST"])
def pack_insights_collect():
    """Collect learnings from agents."""
    return jsonify(collect_from_agents())


@pack_insights_bp.route("/pack-insights/add", methods=["POST"])
def pack_insights_add():
    """Add a manual learning during review."""
    data = request.get_json()
    result = add_manual_learning(data["text"], data.get("category", "domain_knowledge"))
    return jsonify(result)


@pack_insights_bp.route("/pack-insights/synthesize", methods=["POST"])
def pack_insights_synthesize():
    """Run synthesis (dedupe, conflict detection, propose rules)."""
    return jsonify(synthesize())


@pack_insights_bp.route("/pack-insights/finalize", methods=["POST"])
def pack_insights_finalize():
    """Finalize and merge learnings into the global pool."""
    data = request.get_json(silent=True) or {}
    decisions = data.get("decisions", {})
    return jsonify(finalize(decisions))


@pack_insights_bp.route("/pack-insights/reset", methods=["POST"])
def pack_insights_reset():
    """Reset Pack Insights state back to idle."""
    reset()
    return jsonify({"status": "idle"})


@pack_insights_bp.route("/pack-insights/gathering-replies", methods=["GET"])
def pack_insights_gathering_replies():
    """Per-agent view of the current (or most recent) gather.

    Powers the campfire UI: returns the list of agents Maiko messaged
    when this gather started, each with their display info plus any
    feedback / insight replies they've sent since. The frontend polls
    this so new speech bubbles can fade in as agents respond.

    State classification:
        shared  — at least one feedback or insight reply since gather
        quiet   — replied with message_type=status (e.g. "nothing new")
        waiting — messaged, no reply yet

    Replies are stripped to feedback + insight only; status replies are
    captured via the `state` label so the UI doesn't render a bubble
    for "nothing new".
    """
    state = get_state()
    if not state.get("triggered_at"):
        return jsonify({"status": state.get("status", "idle"), "started_at": None, "agents": []})

    from planet_maiko.database import db
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_message import AgentMessage
    from planet_maiko.models.agent_profile import AgentProfile

    # SQLAlchemy stores naive datetimes in SQLite; strip tzinfo for the
    # comparison so "aware vs naive" doesn't raise.
    triggered_utc = datetime.fromisoformat(state["triggered_at"]).replace(tzinfo=None)

    requests = AgentMessage.query.filter(
        AgentMessage.message_type == "pack_insights_request",
        AgentMessage.created_at >= triggered_utc,
    ).all()
    task_ids = list({m.task_id for m in requests})

    if not task_ids:
        return jsonify({
            "status": state.get("status"),
            "started_at": state["triggered_at"],
            "agents": [],
        })

    tasks = {t.id: t for t in Task.query.filter(Task.id.in_(task_ids)).all()}
    profile_ids = list({t.assigned_agent_id for t in tasks.values() if t.assigned_agent_id})
    profiles = {p.id: p for p in AgentProfile.query.filter(AgentProfile.id.in_(profile_ids)).all()}

    reply_rows = (
        AgentMessage.query
        .filter(
            AgentMessage.task_id.in_(task_ids),
            AgentMessage.direction == "from_agent",
            AgentMessage.created_at >= triggered_utc,
            AgentMessage.message_type.in_(("feedback", "insight", "status", "summary")),
        )
        .order_by(AgentMessage.created_at.asc())
        .all()
    )
    replies_by_task = {}
    for r in reply_rows:
        replies_by_task.setdefault(r.task_id, []).append(r)

    agents = []
    for tid, t in tasks.items():
        if not t.assigned_agent_id:
            continue
        prof = profiles.get(t.assigned_agent_id)
        t_replies = replies_by_task.get(tid, [])
        substantive = [r for r in t_replies if r.message_type in ("feedback", "insight")]
        has_substance = bool(substantive)
        any_status = any(r.message_type == "status" for r in t_replies)
        state_label = "shared" if has_substance else ("quiet" if any_status else "waiting")

        # Agent's own one-liner for the campfire bubble. Most recent
        # summary reply wins if they sent multiple.
        summary = None
        for r in t_replies:
            if r.message_type == "summary" and r.content:
                summary = r.content.strip()

        agents.append({
            "agent_id": t.assigned_agent_id,
            "display_name": prof.display_name if prof else t.assigned_agent_id,
            "avatar": (prof.avatar if prof else "shiba"),
            "task_id": tid,
            "task_title": t.title,
            "state": state_label,
            "summary": summary,
            "replies": [
                {
                    "id": r.id,
                    "type": r.message_type,
                    "content": r.content,
                    "created_at": iso_utc(r.created_at),
                }
                for r in substantive
            ],
        })

    agents.sort(key=lambda a: (a["display_name"] or "").lower())
    return jsonify({
        "status": state.get("status"),
        "started_at": state["triggered_at"],
        "agents": agents,
    })


@pack_insights_bp.route("/pack-insights/wrap-up", methods=["POST"])
def pack_insights_wrap_up():
    """Finish the current gather, applying the user's drop decisions.

    Body: { dropped_message_ids: [int, ...] }
    For each dropped reply id, removes the Signal (for feedback replies)
    or dismisses the Insight (for insight replies) that got written when
    the reply landed. Kept replies stay as-is — they were already
    written at reply time, so there's nothing more to do for them.
    Resets the gather state to idle so the next ritual starts clean.
    """
    from planet_maiko.database import db
    from planet_maiko.models.signal import Signal
    from planet_maiko.models.insight import Insight

    data = request.get_json(silent=True) or {}
    dropped_ids = [int(i) for i in data.get("dropped_message_ids", []) if str(i).isdigit()]

    signals_deleted = 0
    insights_dismissed = 0

    if dropped_ids:
        # Signals: hard delete since they haven't been aggregated /
        # incorporated into a Learning yet (Pack Insights commits within
        # a ritual, before the clustering cycle runs).
        sig_q = Signal.query.filter(Signal.source_message_id.in_(dropped_ids))
        signals_deleted = sig_q.count()
        sig_q.delete(synchronize_session=False)

        # Insights: soft dismiss so the history is preserved in the DB.
        ins_q = Insight.query.filter(
            Insight.source_message_id.in_(dropped_ids),
            Insight.status != "dismissed",
        )
        for ins in ins_q.all():
            ins.status = "dismissed"
            insights_dismissed += 1

        db.session.commit()

    reset()
    return jsonify({
        "status": "idle",
        "signals_deleted": signals_deleted,
        "insights_dismissed": insights_dismissed,
    })
