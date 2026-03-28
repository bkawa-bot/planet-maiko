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


def _ensure_columns():
    """Add columns that db.create_all() won't add to existing tables."""
    migrations = [
        "ALTER TABLE tasks ADD COLUMN assigned_agent_id VARCHAR(128)",
        "ALTER TABLE custom_skills ADD COLUMN schedule_interval_minutes INTEGER",
        "ALTER TABLE custom_skills ADD COLUMN creates_pupdates BOOLEAN DEFAULT 0",
        "ALTER TABLE custom_skills ADD COLUMN last_run_at DATETIME",
    ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
        except Exception:
            pass
    db.session.commit()


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
    from planet_maiko.api.eod_api import eod_bp
    from planet_maiko.api.focus_api import focus_bp
    from planet_maiko.api.scene_api import scene_bp
    from planet_maiko.api.profiles_api import profiles_bp
    app.register_blueprint(pupdates_bp, url_prefix="/api")
    app.register_blueprint(tasks_bp, url_prefix="/api")
    app.register_blueprint(projects_bp, url_prefix="/api")
    app.register_blueprint(config_bp, url_prefix="/api")
    app.register_blueprint(brain_bp, url_prefix="/api")
    app.register_blueprint(agents_bp, url_prefix="/api")
    app.register_blueprint(learning_bp, url_prefix="/api")
    app.register_blueprint(eod_bp, url_prefix="/api")
    app.register_blueprint(focus_bp, url_prefix="/api")
    app.register_blueprint(scene_bp, url_prefix="/api")
    app.register_blueprint(profiles_bp, url_prefix="/api")

    # Create tables on first run
    with app.app_context():
        from planet_maiko.models.pupdate import Pupdate  # noqa: F401
        from planet_maiko.models.task import Task  # noqa: F401
        from planet_maiko.models.project import Project  # noqa: F401
        from planet_maiko.models.agent_message import AgentMessage  # noqa: F401
        from planet_maiko.models.signal import Signal  # noqa: F401
        from planet_maiko.models.learning import Learning  # noqa: F401
        from planet_maiko.models.agent_profile import AgentProfile  # noqa: F401
        from planet_maiko.models.context_selection import ContextSelection  # noqa: F401
        from planet_maiko.models.skill_result import SkillResult  # noqa: F401
        from planet_maiko.models.custom_skill import CustomSkill  # noqa: F401
        db.create_all()

        # Schema migrations for existing DBs (SQLite ALTER TABLE is safe)
        _ensure_columns()

        # Seed default skills on first run
        from planet_maiko.agents.skills import seed_defaults
        seed_defaults()

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
