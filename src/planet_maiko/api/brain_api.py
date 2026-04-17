from flask import Blueprint, jsonify, request
from planet_maiko.brain.cycle import run, get_status
from planet_maiko.brain.pupdates.rules import load_rules
from planet_maiko.brain.tasks.scheduler import (
    compute_schedule, set_override, clear_override, get_override,
)
from planet_maiko.brain.guardrails import get_permission_level
from planet_maiko.models.task import Task

brain_bp = Blueprint("brain", __name__)


@brain_bp.route("/brain/status", methods=["GET"])
def brain_status():
    """Get brain cycle status."""
    return jsonify(get_status())


@brain_bp.route("/brain/rules", methods=["GET"])
def brain_rules():
    """Get the current rules."""
    rules = load_rules()
    return jsonify(rules)


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


@brain_bp.route("/system/health", methods=["GET"])
def system_health():
    """Lightweight health snapshot for the topbar indicator.

    Returns per-poller status, last brain-cycle time, and the most
    recent backup. The UI uses this to decide if the health dot is
    green (everything fresh, no errors) / yellow (stale or recent
    error) / red (scheduler not running).
    """
    from flask import current_app
    from planet_maiko.backups import latest_backup

    scheduler = current_app.config.get("SCHEDULER")
    if scheduler is None:
        return jsonify({
            "scheduler_running": False,
            "pollers": {},
            "last_brain_cycle": None,
            "latest_backup": None,
        })

    return jsonify({
        "scheduler_running": True,
        "pollers": dict(scheduler.poller_status),
        "last_brain_cycle": scheduler.last_brain_cycle,
        "latest_backup": latest_backup(),
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
