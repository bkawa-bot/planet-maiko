import os
import logging
import sqlite3
from flask import Flask, send_from_directory
from flask_cors import CORS
from sqlalchemy import event
from sqlalchemy.engine import Engine
from planet_maiko.database import db
from planet_maiko.paths import db_path as get_db_path, static_dir, ensure_dirs, config_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


# SQLite tuning — dramatically reduces lock-contention errors during
# backfills + clustering. Applied on every new connection.
#
# journal_mode=WAL  — write-ahead log. Default is DELETE which takes a
#   process-wide file lock on every write, blocking readers AND other
#   writers for the duration. WAL lets readers keep going while a
#   writer is active and serializes writers cleanly without the shared
#   lock. This is the #1 fix for "database is locked" errors on a
#   multi-thread Flask + SQLite setup.
# busy_timeout=30000 — if a writer arrives while another write is in
#   flight (WAL serializes writers), wait up to 30s for the lock
#   instead of erroring immediately. Matches the longest LLM call
#   we'd hold a transaction across (120s clustering is capped below).
# synchronous=NORMAL — WAL makes NORMAL safe (fsync at checkpoint
#   rather than every commit) and takes ~5x off commit latency. The
#   only exposure is that a power-loss mid-commit can lose the last
#   in-flight transaction, which for a local dev tool is fine.
# foreign_keys=ON — SQLite disables FK enforcement by default; we
#   use FKs on AgentJob → Task and everywhere else, and want them
#   actually enforced.
@event.listens_for(Engine, "connect")
def _sqlite_tuning(dbapi_connection, _connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


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
        # Stable source-system id for dedup. Synthesis mutates signal.text,
        # so the older text-based dedup silently failed on re-scrape.
        "ALTER TABLE signals ADD COLUMN external_id VARCHAR(64)",
        "CREATE INDEX IF NOT EXISTS ix_signals_external_id ON signals(external_id)",
        # Preserve the raw comment body before synthesis rewrites
        # signal.text to a cleaner rule. Used by the provenance UI.
        "ALTER TABLE signals ADD COLUMN original_text TEXT",
        "ALTER TABLE learnings ADD COLUMN is_global BOOLEAN DEFAULT 0",
        "ALTER TABLE custom_skills ADD COLUMN user_edited BOOLEAN DEFAULT 0",
        "ALTER TABLE agent_profiles ADD COLUMN extra JSON DEFAULT '{}'",
        # Stage-5 unification: rules folded into Automations. New
        # column distinguishes cycle-level watches from per-pupdate
        # rules.
        "ALTER TABLE automations ADD COLUMN execution_scope VARCHAR(20) DEFAULT 'cycle'",
        # Pupdate.read retired — no inbox, no mark-as-read concept.
        # SQLite supports DROP COLUMN since 3.35 (2021). Wrap in try/
        # except upstream in case the runtime is older.
        "ALTER TABLE pupdates DROP COLUMN read",
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
        # Self-specialization scoring removed in favor of LoRA-per-repo;
        # ContextSelection was only ever read by the now-gone
        # record_task_outcome / record_session_feedback functions.
        "DROP TABLE IF EXISTS context_selections",
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


def _reconcile_agent_profile_counts():
    """Backfill AgentProfile.tasks_completed / tasks_failed from the
    actual Task + AgentJob history. The counters only moved via the
    legacy one-shot Task path historically, so post-Stage D profiles
    showed stale numbers (most "done" work is on AgentJobs now).
    Runs once per boot; idempotent since it sets the counters to the
    computed truth every time.
    """
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.models.task import Task
    from sqlalchemy import func

    # Done tasks per profile.
    done_tasks = dict(
        Task.query
        .with_entities(Task.assigned_agent_id, func.count(Task.id))
        .filter(Task.status == "done")
        .filter(Task.assigned_agent_id.isnot(None))
        .group_by(Task.assigned_agent_id)
        .all()
    )
    # Done + failed AgentJobs per profile.
    done_jobs = dict(
        AgentJob.query
        .with_entities(AgentJob.agent_profile_id, func.count(AgentJob.id))
        .filter(AgentJob.status == "done")
        .filter(AgentJob.agent_profile_id.isnot(None))
        .group_by(AgentJob.agent_profile_id)
        .all()
    )
    failed_jobs = dict(
        AgentJob.query
        .with_entities(AgentJob.agent_profile_id, func.count(AgentJob.id))
        .filter(AgentJob.status.in_(("failed", "cancelled")))
        .filter(AgentJob.agent_profile_id.isnot(None))
        .group_by(AgentJob.agent_profile_id)
        .all()
    )

    fixed = 0
    for p in AgentProfile.query.all():
        total_done = done_tasks.get(p.id, 0) + done_jobs.get(p.id, 0)
        total_failed = failed_jobs.get(p.id, 0)
        if p.tasks_completed != total_done or p.tasks_failed != total_failed:
            p.tasks_completed = total_done
            p.tasks_failed = total_failed
            fixed += 1
    if fixed:
        db.session.commit()
        logger.info(
            f"[startup] Reconciled done/failed counts on {fixed} agent profile(s)"
        )


def _reconcile_learning_signal_counts():
    """Backfill Learning.signal_count from the actual Signal rows.

    Historical cluster merges and dismissals left many learnings with a
    cached signal_count that no longer matched reality — users saw
    "2 signals" on a card but got an empty list on drill-down.
    Re-sync on startup so the row count and the expansion agree.
    """
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.signal import Signal
    from sqlalchemy import func

    actual = dict(
        Signal.query
        .with_entities(Signal.learning_id, func.count(Signal.id))
        .filter(Signal.learning_id.isnot(None))
        .group_by(Signal.learning_id)
        .all()
    )

    fixed = 0
    for l in Learning.query.all():
        real = actual.get(l.id, 0)
        if l.signal_count != real:
            l.signal_count = real
            fixed += 1
    if fixed:
        db.session.commit()
        logger.info(f"[startup] Reconciled signal_count on {fixed} learning(s)")


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
    from planet_maiko.api.automations_api import automations_bp
    from planet_maiko.api.agent_jobs_api import agent_jobs_bp
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
    app.register_blueprint(automations_bp, url_prefix="/api")
    app.register_blueprint(agent_jobs_bp, url_prefix="/api")

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
        from planet_maiko.models.skill_result import SkillResult  # noqa: F401
        from planet_maiko.models.custom_skill import CustomSkill  # noqa: F401
        from planet_maiko.models.diff_comment import DiffComment  # noqa: F401
        from planet_maiko.models.insight import Insight  # noqa: F401
        from planet_maiko.models.external_session import ExternalSession  # noqa: F401
        from planet_maiko.models.pet import Pet  # noqa: F401
        from planet_maiko.models.automation import Automation  # noqa: F401
        from planet_maiko.models.agent_job import AgentJob  # noqa: F401
        # Legacy AgentGoal kept imported only so the one-time migration
        # below can see its rows on first boot after this upgrade.
        # Remove the import and the table once the migration has run on
        # every install that cares. Harmless while present.
        try:
            from planet_maiko.models.agent_goal import AgentGoal  # noqa: F401
        except Exception:
            pass
        db.create_all()

        # Schema migrations for existing DBs (SQLite ALTER TABLE is safe)
        _ensure_columns()

        # Seed default skills on first run
        from planet_maiko.agents.skills import seed_defaults
        seed_defaults()

        # Unify the autonomy surface: migrate legacy AgentGoal rows
        # into Automations (one-time, idempotent no-op after first
        # successful run), then make sure every configured repo has a
        # seeded 'keep overview current' watch.
        from planet_maiko.brain.automations import (
            migrate_agent_goals, ensure_seed_automations,
            ensure_seed_chain_automations, migrate_scheduled_skills,
            ensure_seed_rule_automations, migrate_legacy_action_kinds,
            migrate_tasks_to_agent_jobs, ensure_plugin_default_automations,
            migrate_per_repo_overview_watches,
            migrate_archive_retired_chain_seeds,
        )
        try:
            migrate_agent_goals()
        except Exception as e:
            logger.warning(f"[startup] AgentGoal migration skipped: {e}")
        try:
            migrate_scheduled_skills()
        except Exception as e:
            logger.warning(f"[startup] Scheduled-skill migration skipped: {e}")
        try:
            ensure_seed_automations()
        except Exception as e:
            logger.warning(f"[startup] Automation seeding skipped: {e}")
        try:
            migrate_per_repo_overview_watches()
        except Exception as e:
            logger.warning(f"[startup] Per-repo overview-watch migration skipped: {e}")
        try:
            migrate_archive_retired_chain_seeds()
        except Exception as e:
            logger.warning(f"[startup] Retired-chain-seed archive skipped: {e}")
        try:
            ensure_seed_chain_automations()
        except Exception as e:
            logger.warning(f"[startup] Chain automation seeding skipped: {e}")
        try:
            ensure_seed_rule_automations()
        except Exception as e:
            logger.warning(f"[startup] Rule automation seeding skipped: {e}")
        try:
            migrate_legacy_action_kinds()
        except Exception as e:
            logger.warning(f"[startup] Legacy action-kind migration skipped: {e}")
        try:
            migrate_tasks_to_agent_jobs()
        except Exception as e:
            logger.warning(f"[startup] Task→AgentJob migration skipped: {e}")
        try:
            _reconcile_learning_signal_counts()
        except Exception as e:
            logger.warning(f"[startup] Learning signal-count reconcile skipped: {e}")
        try:
            _reconcile_agent_profile_counts()
        except Exception as e:
            logger.warning(f"[startup] Agent profile count reconcile skipped: {e}")
        try:
            # Self-heals demo DBs where the seed wrote a signal_count
            # but never populated matching Signal rows. No-op on
            # non-seed databases — matches on aggregation_key.
            from planet_maiko.seed import backfill_seed_signals
            backfill_seed_signals(app)
        except Exception as e:
            logger.warning(f"[startup] Seed signal backfill skipped: {e}")
        try:
            ensure_plugin_default_automations()
        except Exception as e:
            logger.warning(f"[startup] Plugin automation seeding skipped: {e}")

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
