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
    """Add columns and drop dead tables that db.create_all() won't manage."""
    migrations = [
        "ALTER TABLE tasks ADD COLUMN assigned_agent_id VARCHAR(128)",
        "ALTER TABLE tasks ADD COLUMN due_date VARCHAR(20)",
        "ALTER TABLE tasks ADD COLUMN depends_on JSON DEFAULT '[]'",
        "ALTER TABLE custom_skills ADD COLUMN schedule_interval_minutes INTEGER",
        "ALTER TABLE custom_skills ADD COLUMN creates_pupdates BOOLEAN DEFAULT 0",
        "ALTER TABLE custom_skills ADD COLUMN last_run_at DATETIME",
        "ALTER TABLE signals ADD COLUMN code_context TEXT",
        "ALTER TABLE signals ADD COLUMN incorporated_at DATETIME",
        "ALTER TABLE signals ADD COLUMN examples JSON DEFAULT '[]'",
        "ALTER TABLE signals ADD COLUMN synthesized BOOLEAN DEFAULT 0",
        "ALTER TABLE learnings ADD COLUMN is_global BOOLEAN DEFAULT 0",
        "ALTER TABLE custom_skills ADD COLUMN user_edited BOOLEAN DEFAULT 0",
        "ALTER TABLE agent_profiles ADD COLUMN extra JSON DEFAULT '{}'",
        "ALTER TABLE agent_profiles ADD COLUMN role VARCHAR(32) DEFAULT 'coding'",
        "ALTER TABLE agent_profiles ADD COLUMN scope_repo VARCHAR(256)",
        "ALTER TABLE agent_profiles ADD COLUMN instructions TEXT",
        "ALTER TABLE agent_profiles ADD COLUMN state VARCHAR(16) DEFAULT 'idle'",
        "ALTER TABLE pupdates ADD COLUMN category VARCHAR(16) DEFAULT 'activity'",
        # Pack Insights ritual: link signals / insights back to the
        # agent reply they came from so "drop this during review"
        # can undo them cleanly.
        "ALTER TABLE signals ADD COLUMN source_message_id INTEGER",
        "ALTER TABLE insights ADD COLUMN source_message_id INTEGER",
        # Phase A of the external-orchestrator surface — A2A awareness
        # for sessions Maiko didn't spawn. db.create_all() already makes
        # this from the model on fresh installs; the explicit CREATE
        # covers existing DBs that predate the model import.
        (
            "CREATE TABLE IF NOT EXISTS external_sessions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id VARCHAR(128) NOT NULL UNIQUE, "
            "consumer VARCHAR(64), "
            "repo VARCHAR(256) NOT NULL, "
            "worktree_path VARCHAR(1024) NOT NULL, "
            "hint TEXT, "
            "registered_at DATETIME NOT NULL, "
            "completed_at DATETIME, "
            "status VARCHAR(32) NOT NULL DEFAULT 'active', "
            "extra JSON DEFAULT '{}'"
            ")"
        ),
        (
            "CREATE INDEX IF NOT EXISTS ix_external_sessions_session_id "
            "ON external_sessions(session_id)"
        ),
        # Tournament system removed — drop legacy tables if present
        "DROP TABLE IF EXISTS tournament_entries",
        "DROP TABLE IF EXISTS tournaments",
    ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
        except Exception:
            pass

    # Backfill category for rows that pre-date the column. ACTION_TYPES
    # lives on the model so this list doesn't drift.
    try:
        from planet_maiko.models.pupdate import ACTION_TYPES
        action_list = ",".join(f"'{t}'" for t in sorted(ACTION_TYPES))
        db.session.execute(db.text(
            f"UPDATE pupdates SET category = 'action' "
            f"WHERE category IS NULL OR category = '' OR "
            f"(category = 'activity' AND type IN ({action_list}))"
        ))
        db.session.execute(db.text(
            f"UPDATE pupdates SET category = 'activity' "
            f"WHERE (category IS NULL OR category = '') AND type NOT IN ({action_list})"
        ))
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
    from planet_maiko.api.pack_insights_api import pack_insights_bp
    from planet_maiko.api.insights_api import insights_bp
    from planet_maiko.api.focus_api import focus_bp
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
    from planet_maiko.api.sessions_api import sessions_bp
    from planet_maiko.api.home_api import home_bp
    from planet_maiko.api.pack_api import pack_bp
    from planet_maiko.api.checks_api import checks_bp
    from planet_maiko.api.pet_api import pet_bp
    app.register_blueprint(pupdates_bp, url_prefix="/api")
    app.register_blueprint(tasks_bp, url_prefix="/api")
    app.register_blueprint(projects_bp, url_prefix="/api")
    app.register_blueprint(config_bp, url_prefix="/api")
    app.register_blueprint(brain_bp, url_prefix="/api")
    app.register_blueprint(agents_bp, url_prefix="/api")
    app.register_blueprint(learning_bp, url_prefix="/api")
    app.register_blueprint(pack_insights_bp, url_prefix="/api")
    app.register_blueprint(insights_bp, url_prefix="/api")
    app.register_blueprint(focus_bp, url_prefix="/api")
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
    app.register_blueprint(sessions_bp, url_prefix="/api")
    app.register_blueprint(home_bp, url_prefix="/api")
    app.register_blueprint(pack_bp, url_prefix="/api")
    app.register_blueprint(checks_bp, url_prefix="/api")
    app.register_blueprint(pet_bp, url_prefix="/api")

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
        from planet_maiko.models.context_selection import ContextSelection  # noqa: F401
        from planet_maiko.models.skill_result import SkillResult  # noqa: F401
        from planet_maiko.models.custom_skill import CustomSkill  # noqa: F401
        from planet_maiko.models.diff_comment import DiffComment  # noqa: F401
        from planet_maiko.models.insight import Insight  # noqa: F401
        from planet_maiko.models.external_session import ExternalSession  # noqa: F401
        from planet_maiko.models.pet import Pet  # noqa: F401
        db.create_all()

        # Schema migrations for existing DBs (SQLite ALTER TABLE is safe)
        _ensure_columns()

        # Seed default skills on first run
        from planet_maiko.agents.skills import seed_defaults
        seed_defaults()

        # Wake-registry cleanup: the previous run may have crashed with
        # agents flagged "working" and with session-registry entries
        # pointing at tasks that have since been cancelled or merged.
        # Neither survives a crash, so clear them before we start
        # accepting new triggers.
        from planet_maiko.agents.wake import validate_registry, reset_stale_working
        validate_registry()
        reset_stale_working()

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
