"""Ask Maiko — conversational assistant with full system awareness."""

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from planet_maiko.database import db

logger = logging.getLogger(__name__)

chat_bp = Blueprint("chat", __name__)

def _load_voice():
    """Load Maiko's voice reference from prompts/voice.md.

    Cached on first load. Silent fallback to empty string if the file
    isn't there so chat still works; the voice just won't be as
    consistent with the other surfaces.
    """
    global _VOICE_CACHE
    try:
        return _VOICE_CACHE
    except NameError:
        pass
    from pathlib import Path
    try:
        path = Path(__file__).resolve().parent.parent / "prompts" / "voice.md"
        _voice = path.read_text(encoding="utf-8")
    except Exception:
        _voice = ""
    globals()["_VOICE_CACHE"] = _voice
    return _voice


def _system_prompt():
    """Assemble Ask Maiko's system prompt with the shared voice reference."""
    return f"""You are Maiko. You live inside Planet Maiko, a personal dashboard that helps your human stay on top of their work. You have access to the current state of their system (tasks, notifications, agent activity, learnings). Use it to give specific, grounded answers. If you don't have enough context, say so rather than guessing.

Write in Maiko's voice, which is defined below. Reference specific tasks, agents, and events by name when relevant. Concise but thorough; give the answer, not a lecture.

{_load_voice()}"""


def _gather_context():
    """Pull current system state for Maiko's awareness."""
    from planet_maiko.models.task import Task
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.learning import Learning

    sections = []

    # Active tasks
    tasks = Task.query.filter(
        Task.status.in_(["new", "in_progress"])
    ).order_by(Task.priority.desc()).limit(15).all()
    if tasks:
        lines = []
        for t in tasks:
            agent = f" (assigned: {t.assigned_agent_id})" if t.assigned_agent_id else ""
            lines.append(f"- [{t.status}] {t.title} (priority: {t.priority}){agent}")
        sections.append("## Active Tasks\n" + "\n".join(lines))

    # Recent pupdates
    pupdates = Pupdate.query.filter_by(dismissed=False).order_by(
        Pupdate.timestamp.desc()
    ).limit(10).all()
    if pupdates:
        lines = []
        for p in pupdates:
            read_mark = "" if p.read else " [unread]"
            lines.append(f"- [{p.source}/{p.type}]{read_mark} {p.title}")
        sections.append("## Recent Notifications\n" + "\n".join(lines))

    # Agent profiles
    profiles = AgentProfile.query.filter(
        (AgentProfile.archived == False) | (AgentProfile.archived == None)
    ).all()
    if profiles:
        lines = []
        for p in profiles:
            scope = f" — {p.scope_repo}" if p.scope_repo else ""
            lines.append(f"- {p.display_name} ({p.role or 'coding'}){scope}")
        sections.append("## Agents\n" + "\n".join(lines))

    # Learning stats
    active_count = Learning.query.filter_by(status="active").count()
    pending_count = Learning.query.filter_by(status="pending").count()
    if active_count or pending_count:
        sections.append(f"## Knowledge Base\n- {active_count} active learnings, {pending_count} pending review")

    return "\n\n".join(sections) if sections else "No data yet — the system is freshly set up."


@chat_bp.route("/chat", methods=["POST"])
def chat():
    """Send a message to Maiko and get a response."""
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"error": "message required"}), 400

    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        return jsonify({"error": "LLM runtime not available. Is Claude Code installed?"}), 503

    context = _gather_context()

    # Release the DB connection before the long LLM call to avoid SQLite locks
    db.session.close()

    prompt = f"""{_system_prompt()}

## Current System State
{context}

## User's Question
{message}"""

    result = runtime.send(prompt, timeout=60, model=resolve_model("chat"))

    if result.get("success"):
        return jsonify({
            "response": result["output"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    else:
        return jsonify({"error": result.get("error", "Failed to get response")}), 500
