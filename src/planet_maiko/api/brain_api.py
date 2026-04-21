from flask import Blueprint, jsonify, request
from planet_maiko.brain.cycle import run, get_status
from planet_maiko.brain.tasks.scheduler import (
    compute_schedule, set_override, clear_override, get_override,
)
from planet_maiko.brain.guardrails import get_permission_level
from planet_maiko.database import db
from planet_maiko.models.task import Task

brain_bp = Blueprint("brain", __name__)


@brain_bp.route("/brain/status", methods=["GET"])
def brain_status():
    """Get brain cycle status."""
    return jsonify(get_status())


@brain_bp.route("/brain/cycle", methods=["POST"])
def trigger_cycle():
    """Manually trigger a brain cycle."""
    from flask import current_app
    results = run(current_app._get_current_object())
    return jsonify(results)


@brain_bp.route("/brain/schedule", methods=["GET"])
def get_schedule():
    """Get the optimized task schedule."""
    return jsonify(compute_schedule())


@brain_bp.route("/brain/schedule/regenerate", methods=["POST"])
def regenerate_schedule():
    """Re-run the focus ordering with an extra free-text user directive.

    Stores the result as an in-memory override (see scheduler.set_override).
    Subsequent GETs to /brain/schedule return the overridden ordering until
    cleared or expired.
    """
    data = request.get_json(silent=True) or {}
    instructions = (data.get("instructions") or "").strip()
    if not instructions:
        return jsonify({"error": "instructions required"}), 400

    tasks = Task.query.filter(Task.status.in_(["new", "in_progress"])).all()
    if not tasks:
        return jsonify({"error": "no active tasks to reorder"}), 400

    task_dicts = [
        {
            "id": t.id,
            "title": t.title,
            "priority": t.priority,
            "status": t.status,
            "type": t.type,
        }
        for t in tasks
    ]

    from planet_maiko.agents.brain_session import reorder_tasks_with_hint
    result = reorder_tasks_with_hint(task_dicts, instructions)
    if not result["success"]:
        return jsonify({"error": result.get("error") or "reorder failed"}), 500

    set_override(instructions, result["ordered_ids"])
    return jsonify(compute_schedule())


@brain_bp.route("/brain/schedule/override", methods=["DELETE"])
def delete_schedule_override():
    """Clear any active focus ordering override."""
    clear_override()
    return jsonify(compute_schedule())


@brain_bp.route("/today", methods=["GET"])
def today_summary():
    """End-of-day audit: what happened in the user's local calendar day.

    Aggregates the loose pieces of the system into a single digest so
    the user can glance at "what did my pack actually do today". Drives
    the Home "Today" card and underpins the Evening Wrap skill.

    All "today" windows are anchored to the user's local midnight —
    respects the user.timezone config if set.
    """
    from datetime import timezone as _tz
    from planet_maiko.config import user_now
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_message import AgentMessage
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.insight import Insight
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile

    now_local = user_now()
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_utc = midnight_local.astimezone(_tz.utc).replace(tzinfo=None)

    # Tasks finished today (status=done OR cancelled, to capture "things
    # you stopped working on" as well as shipped work).
    tasks_done = (
        Task.query
        .filter(Task.status.in_(("done", "cancelled")))
        .filter(Task.updated_at >= midnight_utc)
        .order_by(Task.updated_at.desc())
        .limit(30)
        .all()
    )

    # Agents that replied today — a rough "pack was active" signal.
    active_agent_ids = [
        r[0] for r in (
            db.session.query(Task.assigned_agent_id)
            .join(AgentMessage, AgentMessage.task_id == Task.id)
            .filter(AgentMessage.direction == "from_agent")
            .filter(AgentMessage.created_at >= midnight_utc)
            .filter(Task.assigned_agent_id.isnot(None))
            .distinct()
            .all()
        )
    ]
    agents = []
    if active_agent_ids:
        profiles = AgentProfile.query.filter(AgentProfile.id.in_(active_agent_ids)).all()
        agents = [
            {"id": p.id, "display_name": p.display_name, "avatar": p.avatar, "role": p.role}
            for p in profiles
        ]

    # Learnings harvested today
    learnings_new = (
        Learning.query
        .filter(Learning.created_at >= midnight_utc)
        .order_by(Learning.created_at.desc())
        .limit(10)
        .all()
    )

    # Insights approved today (moved from pending → active)
    insights_active = (
        Insight.query
        .filter(Insight.status == "active")
        .filter(Insight.updated_at >= midnight_utc)
        .order_by(Insight.updated_at.desc())
        .limit(10)
        .all()
    )

    # Auto-investigations fired today
    auto_investigations = []
    inv_tasks = (
        Task.query
        .filter(Task.type == "investigation")
        .filter(Task.created_at >= midnight_utc)
        .order_by(Task.created_at.desc())
        .limit(10)
        .all()
    )
    for t in inv_tasks:
        if (t.extra or {}).get("auto_spawned"):
            auto_investigations.append({
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "pattern": (t.extra or {}).get("pattern") or [],
            })

    # Incidents the correlator detected today (regardless of whether
    # they spawned an auto-investigation — some might've been
    # dismissed before the agent ran).
    incidents_today = (
        Pupdate.query
        .filter(Pupdate.type == "incident")
        .filter(Pupdate.timestamp >= midnight_utc)
        .order_by(Pupdate.timestamp.desc())
        .limit(10)
        .all()
    )

    return jsonify({
        "date": midnight_local.date().isoformat(),
        "tasks_completed": [
            {
                "id": t.id,
                "title": t.title,
                "type": t.type,
                "status": t.status,
                "assigned_agent_id": t.assigned_agent_id,
                "url": t.url,
            }
            for t in tasks_done
        ],
        "agents_active": agents,
        "learnings_harvested": [
            {"id": l.id, "rule": l.rule, "category": l.category, "status": l.status}
            for l in learnings_new
        ],
        "insights_approved": [
            {"id": i.id, "text": i.text, "repo_scope": i.repo_scope, "tags": i.tags or []}
            for i in insights_active
        ],
        "auto_investigations": auto_investigations,
        "incidents_detected": [
            {"id": p.id, "title": p.title, "priority": p.priority, "dismissed": p.dismissed}
            for p in incidents_today
        ],
    })


@brain_bp.route("/system/health", methods=["GET"])
def system_health():
    """Lightweight health snapshot for the topbar indicator.

    Returns per-poller status, last brain-cycle time, the most
    recent backup, and the availability of external tools Maiko
    depends on (claude, gh, git). The UI uses this to decide if the
    health dot is green/yellow/red, and to surface a first-run banner
    when claude isn't installed — otherwise agents just silently
    never start and the new user has no idea why.
    """
    from flask import current_app
    from planet_maiko.backups import latest_backup

    # Tool availability. claude is load-bearing — no claude means no
    # agents work. gh/git are used for repo discovery + worktrees.
    # Reuse the claude_code runtime's fallback-path logic so this
    # report matches what the rest of Maiko actually uses.
    tools = {}
    try:
        from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
        claude_path = ClaudeCodeRuntime()._find_claude()
    except Exception:
        claude_path = None
    tools["claude"] = {"available": bool(claude_path), "path": claude_path}
    import shutil as _shutil
    for name in ("gh", "git"):
        p = _shutil.which(name)
        tools[name] = {"available": bool(p), "path": p}

    scheduler = current_app.config.get("SCHEDULER")
    if scheduler is None:
        return jsonify({
            "scheduler_running": False,
            "pollers": {},
            "last_brain_cycle": None,
            "latest_backup": None,
            "tools": tools,
        })

    return jsonify({
        "scheduler_running": True,
        "pollers": dict(scheduler.poller_status),
        "last_brain_cycle": scheduler.last_brain_cycle,
        "latest_backup": latest_backup(),
        "tools": tools,
    })


@brain_bp.route("/system/shutdown", methods=["POST"])
def shutdown():
    """Gracefully shut down the server (power saving mode)."""
    import threading
    from flask import current_app

    # Stop the scheduler first
    scheduler = current_app.config.get("SCHEDULER")
    if scheduler:
        scheduler.stop()

    def _shutdown():
        import time, os, signal
        time.sleep(1)  # Let the response send first
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"status": "shutting_down"})


@brain_bp.route("/brain/guardrails/<action>", methods=["GET"])
def check_guardrail(action):
    """Check permission level for an action."""
    return jsonify({
        "action": action,
        "level": get_permission_level(action),
    })
