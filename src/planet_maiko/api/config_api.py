from flask import Blueprint, jsonify, request
from planet_maiko.config import load_config, save_config

config_bp = Blueprint("config", __name__)


@config_bp.route("/config", methods=["GET"])
def get_config():
    """Get the current configuration."""
    config = load_config()
    # Redact sensitive fields for the frontend
    safe = {}
    for key, section in config.items():
        if isinstance(section, dict):
            safe[key] = {
                k: ("***" if k in ("api_key", "token") and v else v)
                for k, v in section.items()
            }
        else:
            safe[key] = section
    return jsonify(safe)


@config_bp.route("/config", methods=["PUT"])
def update_config():
    """Update configuration."""
    data = request.get_json()
    config = load_config()

    for key, section in data.items():
        if isinstance(section, dict) and key in config:
            for k, v in section.items():
                # Don't overwrite secrets with the redacted "***"
                if v == "***":
                    continue
                config[key][k] = v
        else:
            config[key] = section

    save_config(config)
    return jsonify({"status": "ok"})


@config_bp.route("/pollers/status", methods=["GET"])
def poller_status():
    """Get status of all pollers."""
    from flask import current_app
    scheduler = current_app.config.get("SCHEDULER")
    if scheduler:
        return jsonify(scheduler.get_status())
    return jsonify({})


@config_bp.route("/pollers/<name>/run", methods=["POST"])
def run_poller(name):
    """Manually trigger a specific poller."""
    from flask import current_app
    scheduler = current_app.config.get("SCHEDULER")
    if not scheduler:
        return jsonify({"error": "Scheduler not running"}), 503

    try:
        created = scheduler.run_once(name)
        return jsonify({"status": "ok", "created": created})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
