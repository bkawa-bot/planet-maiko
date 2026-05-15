from flask import Blueprint, jsonify
from planet_maiko.brain.cycle import run, get_status
from planet_maiko.brain.guardrails import get_permission_level
from planet_maiko.database import db

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

    # Per-plugin poller state. Scheduler is the brain-cycle thread now;
    # "running" means the thread was started in create_app(). Its
    # stop_event lives in BACKGROUND_STOP; absence of that key means we
    # never started the cycle thread.
    from datetime import datetime as _dt, timezone as _tz
    from planet_maiko.plugins.loader import get_plugins
    from planet_maiko.plugins.helpers import PollerPlugin

    pollers = {}
    for p in get_plugins():
        if not isinstance(p, PollerPlugin):
            continue
        last = p._last_polled
        pollers[p.name] = {
            "last_run_at": (
                _dt.fromtimestamp(last, _tz.utc).isoformat()
                if last else None
            ),
        }

    stop_event = current_app.config.get("BACKGROUND_STOP")
    cycle_running = stop_event is not None and not stop_event.is_set()

    return jsonify({
        "scheduler_running": cycle_running,
        "pollers": pollers,
        "last_brain_cycle": current_app.config.get("LAST_BRAIN_CYCLE"),
        "latest_backup": latest_backup(),
        "tools": tools,
    })


@brain_bp.route("/system/shutdown", methods=["POST"])
def shutdown():
    """Gracefully shut down the server (power saving mode)."""
    import threading
    from flask import current_app

    # Stop background threads (brain cycle + backup loop).
    stop_event = current_app.config.get("BACKGROUND_STOP")
    if stop_event:
        stop_event.set()

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
