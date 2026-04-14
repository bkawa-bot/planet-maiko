import sqlite3

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    """Apply concurrency-friendly pragmas to every SQLite connection.

    WAL mode lets readers and writers run concurrently — only
    writer-vs-writer contends. busy_timeout=5000 makes SQLite wait up
    to 5s for a lock instead of raising "database is locked"
    immediately, which covers the transient contention between the
    Flask request threads, pollers, brain cycle, and task scheduler
    all hitting the same file.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
