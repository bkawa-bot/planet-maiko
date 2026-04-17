"""Nightly gzipped SQLite backups.

The SQLite file is the whole brain — tasks, learnings, insights,
agent profiles, everything. Losing it loses years of accumulated
context. This module takes cheap insurance: a gzipped snapshot
once a day, keeping the last 14 by default.

Uses SQLite's online backup API (`sqlite3.Connection.backup`) so
writers don't block during the snapshot — safe to run while the
server is handling traffic. Gzip happens on a temp file that gets
atomically renamed once complete, so a crash mid-backup leaves the
existing snapshots untouched.
"""

import gzip
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from planet_maiko.paths import data_dir, db_path

logger = logging.getLogger(__name__)

BACKUP_DIR_NAME = "backups"
KEEP_DAYS = 14


def backups_dir():
    path = os.path.join(data_dir(), BACKUP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _timestamp_name(reason):
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d-%H%M")
    suffix = f"-{reason}" if reason and reason != "scheduled" else ""
    return f"{stamp}{suffix}.db.gz"


def create_backup(reason="scheduled"):
    """Take a gzipped snapshot of the live DB.

    Returns:
        dict with {path, bytes, created_at, reason} on success, or
        {error: "..."} on failure. Never raises — callers can log
        and continue; failure to back up shouldn't take the server
        down.
    """
    src = db_path()
    if not os.path.exists(src):
        return {"error": f"db not found at {src}"}

    dst_dir = backups_dir()
    filename = _timestamp_name(reason)
    dst_path = os.path.join(dst_dir, filename)
    tmp_plain = dst_path + ".tmp"
    tmp_gz = dst_path + ".tmp.gz"

    try:
        # Online backup → a plain .db file first, then gzip. We can't
        # pipe straight into gzip because sqlite3.Connection.backup
        # needs a real file handle (another sqlite3.Connection).
        source = sqlite3.connect(src)
        dest = sqlite3.connect(tmp_plain)
        try:
            with dest:
                source.backup(dest)
        finally:
            dest.close()
            source.close()

        with open(tmp_plain, "rb") as f_in, gzip.open(tmp_gz, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out, length=1024 * 1024)

        os.remove(tmp_plain)
        os.replace(tmp_gz, dst_path)

        size = os.path.getsize(dst_path)
        logger.info(f"[backups] Snapshot {filename} ({size // 1024} KB, reason={reason})")
        return {
            "path": dst_path,
            "filename": filename,
            "bytes": size,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }
    except Exception as e:
        # Clean up any partial files so the next run isn't confused.
        for leftover in (tmp_plain, tmp_gz):
            try:
                if os.path.exists(leftover):
                    os.remove(leftover)
            except Exception:
                pass
        logger.warning(f"[backups] Snapshot failed: {e}")
        return {"error": str(e)}


def list_backups():
    """Return existing backups newest-first."""
    out = []
    d = backups_dir()
    if not os.path.isdir(d):
        return out
    for name in os.listdir(d):
        if not name.endswith(".db.gz"):
            continue
        full = os.path.join(d, name)
        try:
            st = os.stat(full)
            out.append({
                "filename": name,
                "path": full,
                "bytes": st.st_size,
                "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            })
        except OSError:
            continue
    out.sort(key=lambda b: b["filename"], reverse=True)
    return out


def latest_backup():
    """Most recent snapshot, or None if there are no backups yet."""
    bks = list_backups()
    return bks[0] if bks else None


def prune_old_backups(keep_days=KEEP_DAYS):
    """Delete backups older than keep_days. Returns count removed.

    Keeps the two newest snapshots regardless of age — so a laptop
    that was off for a month still has *something* to restore from
    on first boot back.
    """
    bks = list_backups()
    if len(bks) <= 2:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    removed = 0
    for bk in bks[2:]:
        try:
            created = datetime.fromisoformat(bk["created_at"])
        except Exception:
            continue
        if created < cutoff:
            try:
                os.remove(bk["path"])
                removed += 1
            except OSError as e:
                logger.debug(f"[backups] Could not remove {bk['filename']}: {e}")
    if removed:
        logger.info(f"[backups] Pruned {removed} old snapshot(s) (>{keep_days} days)")
    return removed


def restore_backup(filename):
    """Restore a named backup over the live DB.

    Safety: the current DB is copied aside to `maiko-pre-restore.db`
    before replacement, so a botched restore is recoverable. Caller
    must ensure the server is stopped — restoring while the DB is
    being written will corrupt it.
    """
    src = os.path.join(backups_dir(), filename)
    if not os.path.exists(src):
        return {"error": f"backup not found: {filename}"}

    live = db_path()
    pre = os.path.join(os.path.dirname(live), "maiko-pre-restore.db")

    try:
        if os.path.exists(live):
            shutil.copy2(live, pre)
    except OSError as e:
        return {"error": f"couldn't stash current db: {e}"}

    try:
        tmp = live + ".tmp"
        with gzip.open(src, "rb") as f_in, open(tmp, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out, length=1024 * 1024)
        os.replace(tmp, live)
    except Exception as e:
        return {"error": str(e)}

    logger.info(f"[backups] Restored from {filename}. Previous db stashed at {pre}")
    return {"restored": filename, "previous_db": pre}


def run_daily_loop(stop_event, interval_seconds=None):
    """Background loop: backup + prune, roughly once per day.

    Runs a first backup 5 minutes after startup (gives the server
    time to settle), then every 24 hours after. Driven by a passed-in
    `stop_event` so the scheduler can shut it down cleanly.
    """
    interval = interval_seconds or 24 * 60 * 60
    first_delay = 5 * 60

    if stop_event.wait(first_delay):
        return

    while not stop_event.is_set():
        try:
            create_backup("scheduled")
            prune_old_backups()
        except Exception as e:
            logger.warning(f"[backups] Loop tick failed: {e}")
        if stop_event.wait(interval):
            return
