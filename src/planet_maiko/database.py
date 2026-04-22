import logging
import sqlite3
import time
import traceback
from datetime import timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

db = SQLAlchemy()


def iso_utc(dt):
    """Serialize a datetime as ISO 8601 with an explicit UTC offset.

    SQLite doesn't preserve tzinfo across round-trips, so values stored
    as tz-aware UTC come back naive. Browsers then parse naive ISO
    strings as local time, silently shifting timestamps by the user's
    offset (e.g. a brief saved at 23:30 UTC displays as "yesterday"
    for Pacific users). Re-attaching UTC here keeps the frontend honest.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

# Warn when a single query takes longer than this. SQLite queries on a
# local file should all be sub-100ms; 500ms means something is off
# (table scan, missing index, or — most likely — blocked on a writer).
SLOW_QUERY_MS = 500

# Warn when a transaction stays open longer than this. Long-held
# transactions are the prime cause of "database is locked" under WAL:
# readers are fine, but a slow writer blocks every other writer.
SLOW_TX_MS = 2000


def _short_stack():
    """Closest ~3 non-library frames — enough to identify the caller."""
    frames = traceback.extract_stack()[:-2]
    relevant = [
        f for f in frames
        if "planet_maiko" in (f.filename or "").replace("\\", "/")
    ]
    out = []
    for f in relevant[-3:]:
        fname = f.filename.replace("\\", "/").rsplit("/", 1)[-1]
        out.append(f"{fname}:{f.lineno}")
    return " -> ".join(out) or "<no planet_maiko frame>"


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    """Apply concurrency-friendly pragmas to every SQLite connection.

    WAL mode lets readers and writers run concurrently — only
    writer-vs-writer contends. busy_timeout=30000 makes SQLite wait
    up to 30s for a lock, matching the longest LLM-held transaction
    we'd expect (clustering caps at 120s but commits per batch).

    foreign_keys is intentionally NOT enabled. It's tempting (we
    have FK declarations on AgentJob → Task, Signal → Learning,
    etc.) but existing DBs carry orphan refs from the era before
    enforcement was on — merged-away learnings, deleted tasks
    whose linked jobs still reference them, etc. Turning FK
    enforcement on mid-flight makes every touching write fail with
    "FOREIGN KEY constraint failed" until those orphans are swept
    up. We'll flip this on once there's a boot-time cleanup that
    nulls out orphan FK columns.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


@event.listens_for(Engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._pm_query_start = time.perf_counter()


@event.listens_for(Engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    start = getattr(context, "_pm_query_start", None)
    if start is None:
        return
    elapsed_ms = (time.perf_counter() - start) * 1000
    if elapsed_ms >= SLOW_QUERY_MS:
        stmt = " ".join(statement.split())[:200]
        logger.warning(
            f"[db] slow query {elapsed_ms:.0f}ms @ {_short_stack()} — {stmt}"
        )


@event.listens_for(Engine, "begin")
def _on_tx_begin(conn):
    conn.info["_pm_tx_start"] = time.perf_counter()
    conn.info["_pm_tx_stack"] = _short_stack()


def _check_tx_elapsed(conn, outcome):
    start = conn.info.pop("_pm_tx_start", None)
    stack = conn.info.pop("_pm_tx_stack", "")
    if start is None:
        return
    elapsed_ms = (time.perf_counter() - start) * 1000
    if elapsed_ms >= SLOW_TX_MS:
        logger.warning(
            f"[db] slow tx ({outcome}) {elapsed_ms:.0f}ms @ {stack}"
        )


@event.listens_for(Engine, "commit")
def _on_tx_commit(conn):
    _check_tx_elapsed(conn, "commit")


@event.listens_for(Engine, "rollback")
def _on_tx_rollback(conn):
    _check_tx_elapsed(conn, "rollback")
