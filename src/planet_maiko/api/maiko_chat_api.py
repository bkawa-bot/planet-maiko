"""HTTP surface for the Maiko chat: the controller's conversation
with the user, distinct from per-agent task-scoped chat in
agent_outbox / AgentMessage.

Two routes:
  GET  /api/maiko/messages   recent turns oldest-first
  POST /api/maiko/chat       persist the user turn, kick off background
                             reply generation, return immediately. The
                             frontend polls /maiko/messages to pick up
                             Maiko's reply when it lands. Mirrors the
                             send-and-poll shape that AgentJobPage's
                             agent chat uses.

Conversational only for now (no tool actions). The prompt receives a
read-only snapshot of active agents, active tasks, and enabled
automations so Maiko can reference specifics, but she cannot yet
reassign, automate, or cancel anything from this surface.
"""

from __future__ import annotations

import json
import logging
import pathlib
from threading import Thread

from flask import Blueprint, current_app, jsonify, request

from planet_maiko.database import db
from planet_maiko.models.maiko_message import MaikoMessage

logger = logging.getLogger(__name__)

# Shared voice fragment used by every Maiko-voiced surface (home
# overview, morning brief, this chat). Read once at import; the file
# rarely changes between server starts.
_VOICE_PATH = pathlib.Path(__file__).resolve().parent.parent / "prompts" / "voice.md"
try:
    _VOICE_TEXT = _VOICE_PATH.read_text(encoding="utf-8")
except Exception:
    _VOICE_TEXT = ""

maiko_chat_bp = Blueprint("maiko_chat", __name__)


@maiko_chat_bp.route("/maiko/messages", methods=["GET"])
def list_messages():
    """Recent turns oldest-first so the frontend can render top-to-bottom
    without reversing. `?limit=` caps how many turns come back (default
    100, max 500)."""
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
    except (TypeError, ValueError):
        limit = 100
    rows = (
        MaikoMessage.query
        .order_by(MaikoMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return jsonify([m.to_dict() for m in rows])


@maiko_chat_bp.route("/maiko/chat", methods=["POST"])
def chat():
    """Persist the user turn, kick off background reply generation,
    return the saved user message immediately.

    The reply lands in maiko_messages once the LLM finishes; the
    frontend picks it up via the next /maiko/messages poll. Mirrors
    the inbox-style flow agent jobs use so the UI can stay
    responsive (allow follow-up turns) while Maiko is still thinking
    on a previous one.

    Body: {"content": str}
    Response: {"user": {...message dict...}}
    """
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content is required"}), 400

    user_msg = MaikoMessage(role="user", content=content)
    db.session.add(user_msg)
    db.session.commit()
    user_dict = user_msg.to_dict()

    # Run generation off the request thread so the POST returns in a
    # few ms instead of holding for the full LLM round trip (which
    # can be 30-90s+ on a fat prompt). The daemon flag lets the
    # process shut down without waiting for any in-flight reply.
    app = current_app._get_current_object()
    Thread(
        target=_generate_and_save_reply,
        args=(app, content),
        daemon=True,
    ).start()

    return jsonify({"user": user_dict})


def _generate_and_save_reply(app, user_content: str) -> None:
    """Background worker: run the LLM call and persist the reply.

    Uses app_context so SQLAlchemy + config are reachable after the
    original request returned. Exceptions are caught and saved as a
    Maiko-voiced error row so the polling frontend still surfaces
    something — never let a thinking placeholder hang forever.
    """
    with app.app_context():
        try:
            reply_text = _generate_reply(user_content)
        except Exception as e:
            logger.exception("[maiko-chat] async reply failed: %s", e)
            reply_text = (
                "Something went sideways on my end. Your message saved "
                "but I couldn't put a reply together. Try again in a sec."
            )
        try:
            maiko_msg = MaikoMessage(role="maiko", content=reply_text)
            db.session.add(maiko_msg)
            db.session.commit()
        except Exception:
            logger.exception("[maiko-chat] failed to persist reply")


# ---------------------------------------------------------------------------
# Reply generation
# ---------------------------------------------------------------------------


def _generate_reply(latest_user_message: str) -> str:
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model, resolve_effort
    from planet_maiko.agents.skills import get_skill_prompt
    from planet_maiko.config import load_config, user_now

    runtime = _get_runtime("maiko")
    if not runtime or not runtime.is_available():
        return (
            "My LLM runtime is not wired up right now (Claude Code "
            "needs to be installed and reachable). I will be back once "
            "it is."
        )

    try:
        user_cfg = (load_config().get("user") or {})
    except Exception:
        user_cfg = {}
    user_name = (user_cfg.get("name") or "").strip() or "there"

    prompt = get_skill_prompt("maiko-chat", {
        "user_name": user_name,
        "current_time": user_now().strftime("%I:%M %p"),
        "voice": _VOICE_TEXT,
        "agents": json.dumps(_agents_context(), indent=2, default=str),
        "tasks": json.dumps(_tasks_context(), indent=2, default=str),
        "automations": json.dumps(_automations_context(), indent=2, default=str),
        "history": _history_text(exclude_last_user=True),
        "user_message": latest_user_message,
    })
    if not prompt:
        return "Couldn't load my chat prompt. Check that maiko-chat.md is in prompts/."

    db.session.close()

    # Match the agent-level timeout in agents/profiles.py (240s) rather
    # than the short 45-60s used by quick triage / router calls. Maiko's
    # prompt is large (voice file + agents + tasks + automations + the
    # full chat history), and Opus reasoning over it can run past a
    # minute on its own — capping at 60s meant follow-up turns would
    # time out before she finished thinking.
    result = runtime.send(
        prompt, timeout=240, source="maiko_chat",
        model=resolve_model("maiko"), effort=resolve_effort("maiko"),
    )
    if not result.get("success"):
        err = result.get("error") or "unknown runtime error"
        logger.warning("[maiko-chat] LLM call failed: %s", err)
        return f"My LLM call failed ({err}). Try again?"

    text = (result.get("output") or "").strip()
    if not text:
        return "Tried to reply but got nothing back. Maybe ask again?"
    return text


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------


def _agents_context(limit=10):
    """Worker agents the pack is currently running. Filters to
    AgentJobs in `running` state with their profile name attached.
    """
    try:
        from planet_maiko.models.agent_job import AgentJob
        from planet_maiko.models.agent_profile import AgentProfile

        jobs = (
            AgentJob.query
            .filter(AgentJob.status == "running")
            .order_by(AgentJob.started_at.desc().nullslast())
            .limit(limit)
            .all()
        )
    except Exception:
        return []

    out = []
    for j in jobs:
        name = None
        if j.agent_profile_id:
            prof = db.session.get(AgentProfile, j.agent_profile_id)
            name = prof.display_name if prof else None
        out.append({
            "agent": name or "(unnamed)",
            "kind": j.kind,
            "title": j.title,
            "repo": j.scope_repo,
        })
    return out


def _tasks_context(limit=15):
    """Active tasks (new + in_progress), most recent first."""
    try:
        from planet_maiko.models.task import Task

        rows = (
            Task.query
            .filter(Task.status.in_(("new", "in_progress")))
            .order_by(Task.updated_at.desc())
            .limit(limit)
            .all()
        )
    except Exception:
        return []
    return [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "type": t.type,
            "priority": t.priority,
        }
        for t in rows
    ]


def _automations_context(limit=15):
    """Enabled automation rules (status=active)."""
    try:
        from planet_maiko.models.automation import Automation

        rows = (
            Automation.query
            .filter(Automation.status == "active")
            .order_by(Automation.created_at.desc())
            .limit(limit)
            .all()
        )
    except Exception:
        return []
    return [
        {"id": a.id, "name": a.name, "description": (a.description or "")[:120]}
        for a in rows
    ]


def _history_text(exclude_last_user=False, limit=20):
    """Render the last N turns as a plain-text thread for the prompt.

    We just persisted the user's latest message, so the default excludes
    the most recent user-role row to avoid showing it twice (the prompt
    has a dedicated "Latest from <user_name>" section right after).
    """
    rows = (
        MaikoMessage.query
        .order_by(MaikoMessage.created_at.desc())
        .limit(limit + (1 if exclude_last_user else 0))
        .all()
    )
    rows.reverse()
    if exclude_last_user and rows and rows[-1].role == "user":
        rows = rows[:-1]
    if not rows:
        return "(no prior messages, this is the first turn.)"
    lines = []
    for m in rows:
        speaker = "You" if m.role == "user" else "Maiko"
        lines.append(f"{speaker}: {m.content}")
    return "\n\n".join(lines)
