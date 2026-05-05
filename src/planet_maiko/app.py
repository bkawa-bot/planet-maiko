import os
import logging
from flask import Flask, send_from_directory
from flask_cors import CORS
from planet_maiko.database import db
from planet_maiko.paths import db_path as get_db_path, static_dir, ensure_dirs, config_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)




def create_app(start_scheduler=False):
    app = Flask(__name__)

    # Ensure config/data directories exist
    ensure_dirs()

    # First-run: create default config if missing
    cfg_path = config_path()
    if not os.path.exists(cfg_path):
        from planet_maiko.config import save_config, DEFAULT_CONFIG
        save_config(DEFAULT_CONFIG)
        logger.info(f"Created default config at {cfg_path}")

    # Database config - XDG data directory
    db_path_val = get_db_path()
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path_val}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # CORS for development (React dev server)
    CORS(app)

    # Initialize database
    db.init_app(app)

    # Register API blueprints
    from planet_maiko.api.pupdates import pupdates_bp
    from planet_maiko.api.tasks import tasks_bp
    from planet_maiko.api.projects import projects_bp
    from planet_maiko.api.config_api import config_bp
    from planet_maiko.api.brain_api import brain_bp
    from planet_maiko.api.agents_api import agents_bp
    from planet_maiko.api.learning_api import learning_bp
    from planet_maiko.api.pack_insights_api import pack_insights_bp
    from planet_maiko.api.insights_api import insights_bp
    from planet_maiko.api.scene_api import scene_bp
    from planet_maiko.api.expertise_api import expertise_bp
    from planet_maiko.api.awareness_api import awareness_bp
    from planet_maiko.api.profiles_api import profiles_bp
    from planet_maiko.api.training_api import training_bp
    from planet_maiko.api.chat_api import chat_bp
    from planet_maiko.api.themes_api import themes_bp
    from planet_maiko.api.diff_api import diff_bp
    from planet_maiko.api.lora_api import lora_bp
    from planet_maiko.api.shutdown_api import shutdown_bp
    from planet_maiko.api.home_api import home_bp
    from planet_maiko.api.pack_api import pack_bp
    from planet_maiko.api.checks_api import checks_bp
    from planet_maiko.api.automations_api import automations_bp
    from planet_maiko.api.agent_jobs_api import agent_jobs_bp
    from planet_maiko.api.memos_api import memos_bp
    from planet_maiko.api.rules_api import rules_bp
    app.register_blueprint(pupdates_bp, url_prefix="/api")
    app.register_blueprint(tasks_bp, url_prefix="/api")
    app.register_blueprint(projects_bp, url_prefix="/api")
    app.register_blueprint(config_bp, url_prefix="/api")
    app.register_blueprint(brain_bp, url_prefix="/api")
    app.register_blueprint(agents_bp, url_prefix="/api")
    app.register_blueprint(learning_bp, url_prefix="/api")
    app.register_blueprint(pack_insights_bp, url_prefix="/api")
    app.register_blueprint(insights_bp, url_prefix="/api")
    app.register_blueprint(scene_bp, url_prefix="/api")
    app.register_blueprint(expertise_bp, url_prefix="/api")
    app.register_blueprint(awareness_bp, url_prefix="/api")
    app.register_blueprint(profiles_bp, url_prefix="/api")
    app.register_blueprint(training_bp, url_prefix="/api")
    app.register_blueprint(chat_bp, url_prefix="/api")
    app.register_blueprint(themes_bp, url_prefix="/api")
    app.register_blueprint(diff_bp, url_prefix="/api")
    app.register_blueprint(lora_bp, url_prefix="/api")
    app.register_blueprint(shutdown_bp, url_prefix="/api")
    app.register_blueprint(home_bp, url_prefix="/api")
    app.register_blueprint(pack_bp, url_prefix="/api")
    app.register_blueprint(checks_bp, url_prefix="/api")
    app.register_blueprint(automations_bp, url_prefix="/api")
    app.register_blueprint(agent_jobs_bp, url_prefix="/api")
    app.register_blueprint(memos_bp, url_prefix="/api")
    app.register_blueprint(rules_bp, url_prefix="/api")

    # Register kind-specific memo approve handlers (agent_proposal → task,
    # future: job_approval → AgentJob). Side-effect-only on import.
    from planet_maiko.brain.memo_handlers import register_all as _register_memo_handlers
    _register_memo_handlers()

    # Load plugins (entry_points + ~/.maiko/plugins/)
    from planet_maiko.plugins.loader import load_plugins
    load_plugins(app)

    # Create tables on first run
    with app.app_context():
        from planet_maiko.models.pupdate import Pupdate  # noqa: F401
        from planet_maiko.models.task import Task  # noqa: F401
        from planet_maiko.models.project import Project  # noqa: F401
        from planet_maiko.models.agent_message import AgentMessage  # noqa: F401
        from planet_maiko.models.signal import Signal  # noqa: F401
        from planet_maiko.models.learning import Learning  # noqa: F401
        from planet_maiko.models.agent_profile import AgentProfile  # noqa: F401
        from planet_maiko.models.custom_skill import CustomSkill  # noqa: F401
        from planet_maiko.models.diff_comment import DiffComment  # noqa: F401
        from planet_maiko.models.insight import Insight  # noqa: F401
        from planet_maiko.models.automation import Automation  # noqa: F401
        from planet_maiko.models.agent_job import AgentJob  # noqa: F401
        from planet_maiko.models.memo import Memo  # noqa: F401
        from planet_maiko.models.adapter_eval import AdapterEval  # noqa: F401
        # Fresh-DB shape: every model registered above gets its table
        # via SQLAlchemy. No schema migrations — Maiko targets new
        # installs only, and the legacy ALTER TABLE / DROP TABLE
        # cleanup it used to do is gone.
        db.create_all()

        # Seed default skills on first run
        from planet_maiko.agents.skills import seed_defaults
        seed_defaults()

        # Seed per-repo watches + make sure every configured repo has a
        # seeded 'keep overview current' watch.
        from planet_maiko.brain.automations import (
            ensure_seed_automations,
            ensure_seed_rule_automations,
            ensure_plugin_default_automations,
        )
        try:
            ensure_seed_automations()
        except Exception as e:
            logger.warning(f"[startup] Automation seeding skipped: {e}")
        try:
            ensure_seed_rule_automations()
        except Exception as e:
            logger.warning(f"[startup] Rule automation seeding skipped: {e}")
        try:
            ensure_plugin_default_automations()
        except Exception as e:
            logger.warning(f"[startup] Plugin automation seeding skipped: {e}")
        try:
            from planet_maiko.brain.learning.trainer import reset_stale_training_progress
            reset_stale_training_progress()
        except Exception as e:
            logger.warning(f"[startup] Stale training-progress cleanup skipped: {e}")
        try:
            # RAG retrieval substrate: ensure each active Learning has a
            # current violation_description + embedding. Runs on a daemon
            # thread because the per-rule LLM call adds up to many minutes
            # for a 300-rule corpus, and we don't want to gate Flask
            # readiness on it.
            from planet_maiko.brain.learning.violation_backfill import backfill_in_background
            backfill_in_background(app)
        except Exception as e:
            logger.warning(f"[startup] Violation-description backfill skipped: {e}")

        # Wake-registry cleanup: the previous run may have crashed with
        # agents flagged "working" and with session-registry entries
        # pointing at tasks that have since been cancelled or merged.
        # Neither survives a crash, so clear them before we start
        # accepting new triggers.
        from planet_maiko.agents.wake import validate_registry, reset_stale_working
        validate_registry()
        reset_stale_working()

        # Rescue any profiles still stuck on the Arriving placeholder
        # from a previous-run LLM call that didn't complete.
        from planet_maiko.agents.profiles import recover_stale_arrivals
        recover_stale_arrivals()

    # Serve pre-built frontend static files
    frontend_dir = static_dir()

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_frontend(path):
        if path and os.path.exists(os.path.join(frontend_dir, path)):
            return send_from_directory(frontend_dir, path)
        index = os.path.join(frontend_dir, "index.html")
        if os.path.exists(index):
            return send_from_directory(frontend_dir, "index.html")
        return "Frontend not built. Run: cd frontend && npm run build", 404

    # Start background pollers
    if start_scheduler:
        from planet_maiko.pollers.scheduler import PollerScheduler
        scheduler = PollerScheduler(app)
        app.config["SCHEDULER"] = scheduler
        scheduler.start()

    return app
