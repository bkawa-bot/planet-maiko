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
            AgentMessage.message_type.in_(("feedback", "insight", "status")),
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
        has_substance = any(r.message_type in ("feedback", "insight") for r in t_replies)
        any_status = any(r.message_type == "status" for r in t_replies)
        state_label = "shared" if has_substance else ("quiet" if any_status else "waiting")

        agents.append({
            "agent_id": t.assigned_agent_id,
            "display_name": prof.display_name if prof else t.assigned_agent_id,
            "avatar": (prof.avatar if prof else "shiba"),
            "task_id": tid,
            "task_title": t.title,
            "state": state_label,
            "replies": [
                {
                    "type": r.message_type,
                    "content": r.content,
                    "created_at": iso_utc(r.created_at),
                }
                for r in t_replies
                if r.message_type in ("feedback", "insight")
            ],
        })

    agents.sort(key=lambda a: (a["display_name"] or "").lower())
    return jsonify({
        "status": state.get("status"),
        "started_at": state["triggered_at"],
        "agents": agents,
    })
