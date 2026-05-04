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
        "ALTER TABLE custom_skills ADD COLUMN needs_worktree BOOLEAN DEFAULT 0",
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
        # RAG-retrieval fields. violation_description is Claude-generated
        # text describing what code violates this rule (grounded in
        # historical signals); violation_embedding is its vector for
        # cosine similarity at review time.
        "ALTER TABLE learnings ADD COLUMN violation_description TEXT",
        "ALTER TABLE learnings ADD COLUMN violation_embedding JSON",
        "ALTER TABLE learnings ADD COLUMN violation_description_generated_at DATETIME",
        "ALTER TABLE learnings ADD COLUMN violation_description_signal_count INTEGER",
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
        # Attached specialties — list of CustomSkill IDs. A run picks one
        # to layer on top of the role protocol; no pick = base role only.
        "ALTER TABLE agent_profiles ADD COLUMN specialty_ids JSON DEFAULT '[]'",
        "ALTER TABLE pupdates ADD COLUMN category VARCHAR(16) DEFAULT 'activity'",
        # Pack Insights ritual: link signals / insights back to the
        # agent reply they came from so "drop this during review"
        # can undo them cleanly.
        "ALTER TABLE signals ADD COLUMN source_message_id INTEGER",
        "ALTER TABLE insights ADD COLUMN source_message_id INTEGER",
        # External-orchestrator session registration was removed —
        # Maiko's own pack is the only thing it tracks now.
        "DROP TABLE IF EXISTS external_sessions",
        # Tournament system removed — drop legacy tables if present
        "DROP TABLE IF EXISTS tournament_entries",
        "DROP TABLE IF EXISTS tournaments",
        # Self-specialization scoring removed in favor of LoRA-per-repo;
        # ContextSelection was only ever read by the now-gone
        # record_task_outcome / record_session_feedback functions.
        "DROP TABLE IF EXISTS context_selections",
        # Legacy autonomy — AgentGoal rows were migrated into
        # Automations in a previous release. Table is inert; drop
        # explicitly so fresh installs never materialize it.
        "DROP TABLE IF EXISTS agent_goals",
        # SkillResult was the old DB-backed cache for skill output.
        # Graduated out during the Memo refactor (skill_result memos
        # + home-overview file cache). Nothing reads it anymore.
        "DROP TABLE IF EXISTS skill_results",
        # LoRA eval persistence — one row per evaluate_adapter() call.
        # db.create_all() handles fresh installs; explicit CREATE
        # covers existing DBs where the model was registered after
        # first boot.
        (
            "CREATE TABLE IF NOT EXISTS adapter_evals ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "adapter_path VARCHAR(1024) NOT NULL, "
            "adapter_version VARCHAR(256), "
            "repo VARCHAR(256), "
            "precision FLOAT NOT NULL DEFAULT 0, "
            "recall FLOAT NOT NULL DEFAULT 0, "
            "f1 FLOAT NOT NULL DEFAULT 0, "
            "tp INTEGER NOT NULL DEFAULT 0, "
            "fp INTEGER NOT NULL DEFAULT 0, "
            "fn INTEGER NOT NULL DEFAULT 0, "
            "tn INTEGER NOT NULL DEFAULT 0, "
            "test_count INTEGER NOT NULL DEFAULT 0, "
            "holdout_fraction FLOAT, "
            "per_category JSON DEFAULT '{}', "
            "extra JSON DEFAULT '{}', "
            "created_at DATETIME NOT NULL"
            ")"
        ),
        "CREATE INDEX IF NOT EXISTS ix_adapter_evals_adapter_path ON adapter_evals(adapter_path)",
        "CREATE INDEX IF NOT EXISTS ix_adapter_evals_repo ON adapter_evals(repo)",
        "CREATE INDEX IF NOT EXISTS ix_adapter_evals_f1 ON adapter_evals(f1)",
        "CREATE INDEX IF NOT EXISTS ix_adapter_evals_created_at ON adapter_evals(created_at)",
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
    from planet_maiko.api.pet_api import pet_bp
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
    app.register_blueprint(pet_bp, url_prefix="/api")
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
        from planet_maiko.models.pet import Pet  # noqa: F401
        from planet_maiko.models.automation import Automation  # noqa: F401
        from planet_maiko.models.agent_job import AgentJob  # noqa: F401
        from planet_maiko.models.memo import Memo  # noqa: F401
        from planet_maiko.models.adapter_eval import AdapterEval  # noqa: F401
        db.create_all()

        # Schema migrations for existing DBs (SQLite ALTER TABLE is safe)
        _ensure_columns()

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
            _reconcile_learning_signal_counts()
        except Exception as e:
            logger.warning(f"[startup] Learning signal-count reconcile skipped: {e}")
        try:
            _reconcile_agent_profile_counts()
        except Exception as e:
            logger.warning(f"[startup] Agent profile count reconcile skipped: {e}")
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
