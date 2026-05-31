import os
import logging
from datetime import datetime, timezone

from flask import Flask, send_from_directory
from flask_cors import CORS
from planet_maiko.database import db
from planet_maiko.paths import db_path as get_db_path, static_dir, ensure_dirs, config_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


# Nullable columns added to existing models. Each entry is
# (table_name, column_name, sql_type). _ensure_new_columns walks
# this list, checks PRAGMA table_info, and runs ALTER TABLE ADD
# COLUMN for any missing one.
# Format: column_type must be SQLite-valid (TEXT / INTEGER / etc.).
_PATCH_COLUMNS = [
    ("agent_messages", "recipient", "VARCHAR(50)"),
    ("custom_skills", "deleted_at", "DATETIME"),
    ("custom_skills", "user_edited", "BOOLEAN DEFAULT 0"),
    ("custom_skills", "needs_worktree", "BOOLEAN DEFAULT 0"),
    ("custom_skills", "last_run_at", "DATETIME"),
    ("custom_skills", "protocol_prompt", "TEXT"),
    ("custom_skills", "permission_mode", "VARCHAR(32)"),
    ("agent_types", "requires_scope_repo_clone", "BOOLEAN DEFAULT 0"),
    ("agent_types", "insight_max_length", "INTEGER DEFAULT 2000"),
    # spawn_mode collapses the prior needs_worktree +
    # requires_scope_repo_clone pair. Existing rows get "worktree"
    # as the safe default; the seeder overwrites builtins to the
    # correct value at boot (investigation, cartographer → scratch).
    ("agent_types", "spawn_mode", "VARCHAR(16) DEFAULT 'worktree'"),
    # input_kind: the IN-side typed socket mirroring output_kind.
    # Existing rows default to "task"; the seeder sets the builtins
    # (review→diff, investigation→incident, cartographer→repo).
    ("agent_types", "input_kind", "VARCHAR(20) DEFAULT 'task'"),
    # accepts: the full set of input kinds a role takes (input_kind is
    # the primary). JSON list; existing rows fall back to [input_kind]
    # in to_dict. Added so a coder can explicitly accept a plan / report.
    ("agent_types", "accepts", "JSON"),
    ("learnings", "last_confirmed_at", "DATETIME"),
    # NodeRun.extra: scatter instance bookkeeping (instance index + label).
    ("node_runs", "extra", "JSON"),
]


# Columns to drop from existing DBs. SQLite supports DROP COLUMN
# since 3.35 (March 2021). Best-effort: if the drop fails (column
# doesn't exist, table missing, old SQLite), we log and move on.
_DROP_COLUMNS = [
    ("custom_skills", "schedule_interval_minutes"),
    ("custom_skills", "creates_pupdates"),
    # AgentType schema trim — dead-weight columns that no code path
    # reads. Pass 1 of the post-rogue-agent cleanup.
    ("agent_types", "tagline"),
    ("agent_types", "is_active"),
    ("agent_types", "commits_locally"),
    ("agent_types", "produces_pr"),
    ("agent_types", "is_self_reviewing"),
    ("agent_types", "default_display_name"),
    ("agent_types", "supports_plan_first"),
    # Pass 2: helper-mediated drops (call sites now read constants or
    # role-keyed special cases) and the worktree boolean collapse
    # into spawn_mode.
    ("agent_types", "branch_prefix"),
    ("agent_types", "auto_tag_insights"),
    ("agent_types", "insight_max_length"),
    ("agent_types", "needs_worktree"),
    ("agent_types", "requires_scope_repo_clone"),
]


def _ensure_new_columns():
    """Idempotent ALTER TABLE ADD COLUMN for the small set of
    nullable columns added between releases, so existing user DBs
    don't hit "no such column" after a model change.

    Anything destructive (drops, renames, type changes) still needs
    a fresh DB; this only handles the cheap "I added a nullable
    column" case.

    Logs every patch decision at INFO so a boot trail makes it
    obvious which columns were already present, which were just
    added, and which couldn't be patched (e.g. table doesn't
    exist yet).
    """
    from sqlalchemy import text
    if not _PATCH_COLUMNS:
        return
    logger.info(
        f"[startup] Checking {len(_PATCH_COLUMNS)} patch column(s) on existing DB"
    )
    added = 0
    skipped = 0
    try:
        with db.engine.begin() as conn:
            for table, column, col_type in _PATCH_COLUMNS:
                rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
                existing = {r[1] for r in rows}  # col 1 is name
                if not existing:
                    logger.warning(
                        f"[startup] Table {table!r} not found — patch for "
                        f"{column} skipped (will be picked up on next boot "
                        f"once the table exists)"
                    )
                    continue
                if column in existing:
                    skipped += 1
                    continue
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                ))
                added += 1
                logger.info(
                    f"[startup] Added {table}.{column} ({col_type}) to existing DB"
                )
        logger.info(
            f"[startup] Column-patch pass complete. added {added}, "
            f"already-present {skipped}"
        )
    except Exception as e:
        logger.warning(f"[startup] Column-patch check failed: {e}")


def _drop_legacy_columns():
    """Drop columns the ORM no longer declares. Mirror of
    _ensure_new_columns: scans PRAGMA table_info, runs ALTER TABLE
    DROP COLUMN for any column still present that the model doesn't
    declare.

    Best-effort. SQLite < 3.35 doesn't support DROP COLUMN; if the
    drop fails, the column just sits there as orphan data.
    """
    from sqlalchemy import text
    if not _DROP_COLUMNS:
        return
    try:
        with db.engine.begin() as conn:
            for table, column in _DROP_COLUMNS:
                rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
                existing = {r[1] for r in rows}
                if not existing or column not in existing:
                    continue
                try:
                    conn.execute(text(
                        f"ALTER TABLE {table} DROP COLUMN {column}"
                    ))
                    logger.info(
                        f"[startup] Dropped column {table}.{column}"
                    )
                except Exception as e:
                    logger.warning(
                        f"[startup] Could not drop {table}.{column}: {e}"
                    )
    except Exception as e:
        logger.warning(f"[startup] Column-drop pass failed: {e}")


def _drop_diff_comment_task_fk():
    """One-shot: rebuild diff_comments without the FK to tasks.id.

    The column holds either a Task.id OR an AgentJob.id (review agents
    whose job has no source_task_id can't insert otherwise; leave_comment
    404s with "no task linked to this agent"). SQLite can't drop a FK
    in place, so the standard pattern is recreate-table-without-it.

    Idempotent: checks pragma foreign_key_list first; subsequent
    boots find no FK to drop and exit immediately. Wrapped in a
    transaction so a partial run can't strand data.
    """
    from sqlalchemy import text
    try:
        with db.engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(diff_comments)")).all()
            if not rows:
                return  # Table doesn't exist yet; fresh DB, model is FK-less.
            fks = conn.execute(text("PRAGMA foreign_key_list(diff_comments)")).all()
            # FK row format: (id, seq, table, from, to, on_update, on_delete, match)
            has_task_fk = any(r[2] == "tasks" and r[3] == "task_id" for r in fks)
            if not has_task_fk:
                return
            # SQLite recreate-table idiom: rename old, create new, copy,
            # drop old. Indexes get recreated explicitly. PRAGMA
            # foreign_keys is connection-scoped; we toggle it off for
            # the rebuild so the COPY doesn't trip on existing rows
            # whose task_id doesn't point at a Task (orphaned during
            # review-job-without-task flows).
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(text("ALTER TABLE diff_comments RENAME TO diff_comments_old"))
            conn.execute(text(
                """
                CREATE TABLE diff_comments (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    task_id VARCHAR(128) NOT NULL,
                    file_path VARCHAR(512) NOT NULL,
                    line_number INTEGER NOT NULL,
                    side VARCHAR(3) NOT NULL,
                    base_sha VARCHAR(40),
                    body TEXT NOT NULL,
                    parent_id INTEGER,
                    status VARCHAR(16) NOT NULL,
                    author VARCHAR(8) NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME,
                    FOREIGN KEY(parent_id) REFERENCES diff_comments(id)
                )
                """
            ))
            conn.execute(text(
                "INSERT INTO diff_comments SELECT * FROM diff_comments_old"
            ))
            conn.execute(text("DROP TABLE diff_comments_old"))
            conn.execute(text(
                "CREATE INDEX ix_diff_comments_task_id ON diff_comments(task_id)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_diff_comments_status ON diff_comments(status)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_diff_comments_parent_id ON diff_comments(parent_id)"
            ))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            logger.info(
                "[startup] Dropped FK on diff_comments.task_id (review-agent comments)"
            )
    except Exception as e:
        logger.warning(f"[startup] diff_comments FK drop skipped: {e}")


def _rename_diff_comment_task_to_job():
    """One-shot: rename diff_comments.task_id to diff_comments.job_id
    and backfill any rows that still hold a Task.id to the
    corresponding AgentJob.id.

    Comments live on the AgentJob that owns the diff (one Task can
    spawn coding + review + investigation jobs, each with its own
    diff). SQLite can't rename a column in place on older versions,
    so we rebuild the table.

    Idempotent: checks if `job_id` already exists and exits if so.
    Best-effort: if the rebuild fails the column stays as-is and the
    code path falls back to whichever name SQLAlchemy reflection
    found.
    """
    from sqlalchemy import text
    try:
        with db.engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(diff_comments)")).all()
            if not rows:
                return  # Fresh DB; model already declares job_id.
            cols = {r[1] for r in rows}
            if "job_id" in cols:
                return  # Already renamed.
            if "task_id" not in cols:
                return  # Model and table both already on the new shape.

            # Backfill: for every row whose stored id is a Task.id (not
            # a Job.id), find the Job that owns the diff. Prefer the
            # most recent non-cancelled review/coding job linked to the
            # task. Rows where the id already IS a Job.id pass through
            # unchanged. Rows where neither lookup hits stay on the
            # original id (orphans, kept for history rather than dropped).
            agent_jobs = conn.execute(
                text("SELECT id, source_task_id FROM agent_jobs")
            ).all() if "agent_jobs" in {
                r[0] for r in conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )).all()
            } else []
            job_ids = {j[0] for j in agent_jobs}
            task_to_job = {}
            for jid, source_task_id in agent_jobs:
                if source_task_id and source_task_id not in task_to_job:
                    task_to_job[source_task_id] = jid

            comment_rows = conn.execute(
                text("SELECT id, task_id FROM diff_comments")
            ).all()
            backfilled = 0
            for cid, tid in comment_rows:
                if tid in job_ids:
                    continue  # already a Job.id, nothing to do
                replacement = task_to_job.get(tid)
                if replacement:
                    conn.execute(
                        text("UPDATE diff_comments SET task_id = :j WHERE id = :c"),
                        {"j": replacement, "c": cid},
                    )
                    backfilled += 1

            # SQLite recreate-table idiom for column rename.
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(text("ALTER TABLE diff_comments RENAME TO diff_comments_old"))
            conn.execute(text(
                """
                CREATE TABLE diff_comments (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    job_id VARCHAR(128) NOT NULL,
                    file_path VARCHAR(512) NOT NULL,
                    line_number INTEGER NOT NULL,
                    side VARCHAR(3) NOT NULL,
                    base_sha VARCHAR(40),
                    body TEXT NOT NULL,
                    parent_id INTEGER,
                    status VARCHAR(16) NOT NULL,
                    author VARCHAR(8) NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME,
                    FOREIGN KEY(parent_id) REFERENCES diff_comments(id)
                )
                """
            ))
            conn.execute(text(
                "INSERT INTO diff_comments "
                "(id, job_id, file_path, line_number, side, base_sha, body, "
                " parent_id, status, author, created_at, updated_at) "
                "SELECT id, task_id, file_path, line_number, side, base_sha, "
                "       body, parent_id, status, author, created_at, updated_at "
                "FROM diff_comments_old"
            ))
            conn.execute(text("DROP TABLE diff_comments_old"))
            conn.execute(text(
                "CREATE INDEX ix_diff_comments_job_id ON diff_comments(job_id)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_diff_comments_status ON diff_comments(status)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_diff_comments_parent_id ON diff_comments(parent_id)"
            ))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            logger.info(
                f"[startup] Renamed diff_comments.task_id -> job_id "
                f"({backfilled} row(s) backfilled from Task.id to AgentJob.id)"
            )
    except Exception as e:
        logger.warning(f"[startup] diff_comments column rename skipped: {e}")


def _retro_incubate_thin_pending():
    """One-shot: flip auto-created 1-signal pending learnings to
    incubating so existing DBs match the graduation gate.

    Idempotent: after the first run there's nothing matching the
    WHERE clause, so subsequent boots are a no-op. Manual additions
    (source!='auto') are skipped so a user-curated singleton rule
    stays visible in the approval queue.
    """
    from sqlalchemy import text
    try:
        with db.engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(learnings)")).all()
            cols = {r[1] for r in rows}
            if not cols or "status" not in cols or "signal_count" not in cols:
                return
            result = conn.execute(text(
                "UPDATE learnings SET status = 'incubating' "
                "WHERE status = 'pending' AND signal_count < 2 "
                "AND (source IS NULL OR source = 'auto')"
            ))
            n = result.rowcount or 0
            if n > 0:
                logger.info(
                    f"[startup] Re-incubated {n} thin pending learning(s) — "
                    f"need a second corroborating signal to surface for approval"
                )
    except Exception as e:
        logger.warning(f"[startup] Retro-incubate pass skipped: {e}")


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
    # LoRA verifier + Training UI are parked (lora-park). The
    # training_bp / lora_bp blueprints + their underlying training and
    # inference pipelines stay in the repo dormant; they're just not
    # registered as routes. To revive: re-add the imports +
    # register_blueprint calls.
    from planet_maiko.api.chat_api import chat_bp
    from planet_maiko.api.themes_api import themes_bp
    from planet_maiko.api.diff_api import diff_bp
    from planet_maiko.api.shutdown_api import shutdown_bp
    from planet_maiko.api.home_api import home_bp
    from planet_maiko.api.pack_api import pack_bp
    from planet_maiko.api.maiko_chat_api import maiko_chat_bp
    from planet_maiko.api.usage_api import usage_bp
    from planet_maiko.api.checks_api import checks_bp
    from planet_maiko.api.automations_api import automations_bp
    from planet_maiko.api.agent_jobs_api import agent_jobs_bp
    from planet_maiko.api.agent_types_api import agent_types_bp
    from planet_maiko.api.specialties_api import specialties_bp
    from planet_maiko.api.memos_api import memos_bp
    from planet_maiko.api.rules_api import rules_bp
    from planet_maiko.api.workflows_api import workflows_bp
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
    # training_bp + lora_bp deliberately not registered — see lora-park.
    app.register_blueprint(chat_bp, url_prefix="/api")
    app.register_blueprint(themes_bp, url_prefix="/api")
    app.register_blueprint(diff_bp, url_prefix="/api")
    app.register_blueprint(shutdown_bp, url_prefix="/api")
    app.register_blueprint(home_bp, url_prefix="/api")
    app.register_blueprint(pack_bp, url_prefix="/api")
    app.register_blueprint(maiko_chat_bp, url_prefix="/api")
    app.register_blueprint(usage_bp, url_prefix="/api")
    app.register_blueprint(checks_bp, url_prefix="/api")
    app.register_blueprint(automations_bp, url_prefix="/api")
    app.register_blueprint(agent_jobs_bp, url_prefix="/api")
    app.register_blueprint(agent_types_bp, url_prefix="/api")
    app.register_blueprint(specialties_bp, url_prefix="/api")
    app.register_blueprint(memos_bp, url_prefix="/api")
    app.register_blueprint(rules_bp, url_prefix="/api")
    app.register_blueprint(workflows_bp, url_prefix="/api")

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
        from planet_maiko.models.agent_type import AgentType  # noqa: F401
        from planet_maiko.models.specialty import Specialty  # noqa: F401
        from planet_maiko.models.diff_comment import DiffComment  # noqa: F401
        from planet_maiko.models.insight import Insight  # noqa: F401
        from planet_maiko.models.automation import Automation  # noqa: F401
        from planet_maiko.models.agent_job import AgentJob  # noqa: F401
        from planet_maiko.models.memo import Memo  # noqa: F401
        from planet_maiko.models.adapter_eval import AdapterEval  # noqa: F401
        from planet_maiko.models.workflow import Workflow  # noqa: F401
        from planet_maiko.models.workflow_run import WorkflowRun, NodeRun  # noqa: F401
        # Fresh-DB shape: every model registered above gets its table
        # via SQLAlchemy.
        db.create_all()
        # SQLAlchemy's create_all only creates missing TABLES, not
        # missing COLUMNS. When a model gains a nullable column
        # between commits, existing user DBs hit "no such column" on
        # first query. _ensure_new_columns runs idempotent ALTER
        # TABLE statements for the small set of nullable columns
        # added since launch. Destructive schema changes still
        # require a fresh DB.
        _ensure_new_columns()
        _drop_legacy_columns()
        _drop_diff_comment_task_fk()
        _rename_diff_comment_task_to_job()
        _retro_incubate_thin_pending()

        # Seed default skills on first run
        from planet_maiko.agents.skills import seed_defaults
        seed_defaults()

        # AgentType + Specialty seed pass (issue #22 split). Runs
        # after the CustomSkill seed so the backfill pass sees the
        # default skill rows. Both calls are idempotent.
        try:
            from planet_maiko.agent_types import (
                ensure_seed_agent_types, backfill_from_custom_skills,
            )
            ensure_seed_agent_types()
            backfill_from_custom_skills()
        except Exception as e:
            logger.warning(f"[startup] AgentType seed / backfill skipped: {e}")

        # Seed per-repo watches + make sure every configured repo has a
        # seeded 'keep overview current' watch.
        from planet_maiko.brain.automations import (
            ensure_seed_automations,
            ensure_seed_rule_automations,
            ensure_plugin_default_automations,
            migrate_obsolete_create_task_seeds,
        )
        # Archive obsolete create_task default rows BEFORE seeding the
        # new notify_me equivalents so existing installs don't run
        # both side-by-side for a tick.
        try:
            migrate_obsolete_create_task_seeds()
        except Exception as e:
            logger.warning(f"[startup] Obsolete seed migration skipped: {e}")
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

        # Wake-registry cleanup: a prior run may have crashed with
        # agents flagged "working" and with session-registry entries
        # pointing at tasks that have since been cancelled or merged.
        # Neither survives a crash, so clear them before we start
        # accepting new triggers. Wrapped in try/except so a transient
        # SQLite lock (e.g. another maiko process still holding the DB)
        # can't crash boot. The next cycle's stuck-check will catch up.
        try:
            from planet_maiko.agents.wake import validate_registry, reset_stale_working
            validate_registry()
            reset_stale_working()
        except Exception as e:
            logger.warning(f"[startup] Wake-registry cleanup skipped: {e}")

        # Rescue any profiles still stuck on the Arriving placeholder
        # from a prior LLM call that didn't complete.
        try:
            from planet_maiko.agents.profiles import recover_stale_arrivals
            recover_stale_arrivals()
        except Exception as e:
            logger.warning(f"[startup] Stale arrival rescue skipped: {e}")

        # Catch up any from_agent AgentMessage with recipient="user"
        # that doesn't have a matching Memo yet — covers messages
        # that landed before _emit_user_memo was wired or where the
        # live emission silently failed. Idempotent + bounded to the
        # last 7 days so it can't flood the inbox.
        try:
            from planet_maiko.api.agents_api import backfill_user_message_memos
            backfill_user_message_memos()
        except Exception as e:
            logger.warning(f"[startup] User-message memo backfill skipped: {e}")

        # Violation-description backfill is OPT-IN. RAG retrieval is
        # gated on an embedding backend (offline by default until the
        # user installs sentence-transformers / sets an API key), so
        # generating descriptions before that's set up is wasted work.
        # Triggers that mint descriptions:
        #   - Approving / editing a learning (learning_api hooks)
        #   - POST /api/rules/regenerate-descriptions (manual)
        #   - `maiko lora rules backfill` CLI command
        # If you want it back on boot, call backfill_in_background(app)
        # here, but reset_stale_working at the top of this block
        # races it for the write lock and "database is locked" follows.

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

    # Background threads: brain cycle ticker + nightly DB backups.
    # Pollers used to have their own per-source threads; they're now
    # plugins that fire inside the brain cycle's on_brain_cycle hook,
    # so a single tick drives all polling. See plugins/builtin/.
    if start_scheduler:
        import threading
        import time
        from planet_maiko.config import load_config
        from planet_maiko.brain.cycle import run as run_brain_cycle
        from planet_maiko.backups import run_daily_loop as backup_loop

        cfg = load_config()
        brain_interval = int(cfg.get("brain", {}).get("cycle_interval_minutes", 5)) * 60

        stop_event = threading.Event()
        app.config["BACKGROUND_STOP"] = stop_event
        app.config["BRAIN_INTERVAL_SECONDS"] = brain_interval
        # Tracks last successful brain-cycle timestamp for the home
        # health pane. Single source instead of the old SCHEDULER blob.
        app.config["LAST_BRAIN_CYCLE"] = None

        def _brain_cycle_loop():
            time.sleep(30)  # let the app fully come up before first tick
            while not stop_event.is_set():
                try:
                    with app.app_context():
                        run_brain_cycle(app)
                    app.config["LAST_BRAIN_CYCLE"] = datetime.now(timezone.utc).isoformat()
                except Exception as e:
                    logger.error(f"[brain-cycle] tick failed: {e}")
                for _ in range(brain_interval):
                    if stop_event.is_set():
                        break
                    time.sleep(1)

        threading.Thread(target=_brain_cycle_loop, daemon=True, name="brain-cycle").start()
        logger.info(f"[brain-cycle] ticking every {brain_interval}s")

        threading.Thread(target=backup_loop, args=(stop_event,), daemon=True, name="backups").start()
        logger.info("[backups] daily loop started")

    return app
