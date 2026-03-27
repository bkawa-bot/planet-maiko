from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.agents.brain_session import run_skill, get_status as brain_status
from planet_maiko.agents.coding_agent import prepare, list_prepared, cleanup
from planet_maiko.agents.monitor import get_agent_activity, process_agent_pupdates, get_stuck_agents
from planet_maiko.agents.skills import list_skills

agents_bp = Blueprint("agents", __name__)


@agents_bp.route("/brain/session", methods=["GET"])
def get_brain_session():
    """Get brain session status (runtime info)."""
    return jsonify(brain_status())


@agents_bp.route("/skills", methods=["GET"])
def get_skills():
    """List all available skills."""
    return jsonify(list_skills())


@agents_bp.route("/skills/<skill_name>/run", methods=["POST"])
def run_skill_endpoint(skill_name):
    """Run a skill through the brain session."""
    data = request.get_json() or {}
    context = data.get("context", {})
    working_dir = data.get("working_dir")

    result = run_skill(skill_name, context=context, working_dir=working_dir)
    return jsonify(result)


@agents_bp.route("/agents", methods=["GET"])
def get_agents():
    """List all prepared agent worktrees."""
    return jsonify(list_prepared())


@agents_bp.route("/agents/activity", methods=["GET"])
def get_activity():
    """Get recent agent activity (pupdates from agents)."""
    return jsonify(get_agent_activity())


@agents_bp.route("/agents/stuck", methods=["GET"])
def get_stuck():
    """Get agents that haven't reported in a while."""
    return jsonify(get_stuck_agents())


@agents_bp.route("/agents/process", methods=["POST"])
def process_agents():
    """Process agent pupdates (auto-complete tasks, etc.)."""
    result = process_agent_pupdates()
    return jsonify(result)


@agents_bp.route("/agents/prepare", methods=["POST"])
def prepare_agent():
    """Prepare a worktree for an agent task.

    Does NOT launch the agent - just sets up the worktree with
    TASK.md and CLAUDE.md, and notifies the user it's ready.
    """
    data = request.get_json()
    result = prepare(
        task_id=data["task_id"],
        task_title=data["task_title"],
        prompt=data["prompt"],
        repo_path=data["repo_path"],
        branch_prefix=data.get("branch_prefix", "maiko"),
    )
    if not result:
        return jsonify({"error": "Failed to prepare agent worktree"}), 500
    return jsonify(result), 201


@agents_bp.route("/agents/cleanup", methods=["POST"])
def cleanup_agent():
    """Clean up a worktree after an agent is done."""
    data = request.get_json()
    cleanup(data["repo_path"], data["branch"])
    return jsonify({"status": "cleaned up"})


# --- Agent Inbox (bidirectional communication) ---

@agents_bp.route("/agents/<task_id>/inbox", methods=["GET"])
def get_agent_inbox(task_id):
    """Get messages for an agent (agent polls this).

    Query params:
        unread_only: "true" to only return unread messages (default: true)
        mark_read: "true" to auto-mark returned messages as read (default: true)
    """
    unread_only = request.args.get("unread_only", "true").lower() == "true"
    mark_read = request.args.get("mark_read", "true").lower() == "true"

    query = AgentMessage.query.filter_by(task_id=task_id, direction="to_agent")
    if unread_only:
        query = query.filter_by(read=False)

    messages = query.order_by(AgentMessage.created_at.asc()).all()

    if mark_read and messages:
        for m in messages:
            m.read = True
        db.session.commit()

    return jsonify([m.to_dict() for m in messages])


@agents_bp.route("/agents/<task_id>/inbox", methods=["POST"])
def send_to_agent(task_id):
    """Send a message to an agent (from dashboard, brain, or user)."""
    data = request.get_json()
    msg = AgentMessage(
        task_id=task_id,
        direction="to_agent",
        sender=data.get("sender", "user"),
        content=data["content"],
        message_type=data.get("message_type", "message"),
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify(msg.to_dict()), 201


@agents_bp.route("/agents/<task_id>/outbox", methods=["POST"])
def agent_sends_message(task_id):
    """Agent sends a message back (alternative to pupdate-based reporting)."""
    data = request.get_json()
    msg = AgentMessage(
        task_id=task_id,
        direction="from_agent",
        sender="agent",
        content=data["content"],
        message_type=data.get("message_type", "message"),
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify(msg.to_dict()), 201


@agents_bp.route("/agents/<task_id>/messages", methods=["GET"])
def get_all_messages(task_id):
    """Get full conversation history for a task (both directions)."""
    messages = (
        AgentMessage.query
        .filter_by(task_id=task_id)
        .order_by(AgentMessage.created_at.asc())
        .all()
    )
    return jsonify([m.to_dict() for m in messages])
