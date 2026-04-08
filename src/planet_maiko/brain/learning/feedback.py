"""Feedback processing — resolves pre-commit hook outcomes using git history.

The pre-commit hook logs violations to a local JSONL file. This module:
1. Reads pending feedback entries
2. Uses git log to determine outcomes (accepted = user fixed it, rejected = bypassed)
3. Syncs resolved entries back as Signals for the learning pipeline

Feedback file: ~/.local/share/planet-maiko/feedback/pending.jsonl
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

FEEDBACK_RESOLUTION_WINDOW = timedelta(hours=1)


def _feedback_dir():
    from planet_maiko.paths import data_dir
    return os.path.join(data_dir(), "feedback")


def _pending_path():
    return os.path.join(_feedback_dir(), "pending.jsonl")


def log_feedback(entry):
    """Append a feedback entry to pending.jsonl.

    Called by the pre-commit hook when violations are found.

    Args:
        entry: dict with timestamp, repo, file_path, diff, model_output,
               adapter_path, review_id
    """
    path = _pending_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    entry.setdefault("status", "flagged")

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def resolve_pending_feedback(repo=None, repo_path=None):
    """Read pending.jsonl, use git log to resolve outcomes.

    For each flagged entry:
    - If the flagged file was committed within 1 hour of the flag → "accepted"
      (user fixed the issue and re-committed)
    - Otherwise → "rejected" (user bypassed with --no-verify)

    Args:
        repo: filter to this repo name (e.g. "org/repo")
        repo_path: local path to the repo for git log queries

    Returns:
        dict with {total, accepted, rejected, still_pending}
    """
    path = _pending_path()
    if not os.path.exists(path):
        return {"total": 0, "accepted": 0, "rejected": 0, "still_pending": 0}

    with open(path, "r", encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]

    if not entries:
        return {"total": 0, "accepted": 0, "rejected": 0, "still_pending": 0}

    resolved = []
    still_pending = []
    accepted = 0
    rejected = 0

    for entry in entries:
        if entry.get("status") != "flagged":
            resolved.append(entry)
            continue

        if repo and entry.get("repo") != repo:
            still_pending.append(entry)
            continue

        flag_time = datetime.fromisoformat(entry["timestamp"])
        now = datetime.now(timezone.utc)

        # If flagged less than 1 hour ago, leave as pending
        if now - flag_time < FEEDBACK_RESOLUTION_WINDOW:
            still_pending.append(entry)
            continue

        # Check git log: was the file committed after the flag?
        file_path = entry.get("file_path", "")
        was_committed = False

        if repo_path and file_path:
            try:
                since = flag_time.strftime("%Y-%m-%dT%H:%M:%S")
                result = subprocess.run(
                    ["git", "log", "--oneline", f"--since={since}", "--", file_path],
                    cwd=repo_path, capture_output=True, text=True, timeout=10,
                )
                was_committed = bool(result.stdout.strip())
            except Exception:
                pass

        if was_committed:
            entry["status"] = "accepted"
            entry["resolved_at"] = now.isoformat()
            accepted += 1
        else:
            entry["status"] = "rejected"
            entry["resolved_at"] = now.isoformat()
            rejected += 1

        resolved.append(entry)

    # Rewrite file with resolved + still pending
    all_entries = resolved + still_pending
    with open(path, "w", encoding="utf-8") as f:
        for e in all_entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    logger.info(f"[feedback] Resolved {accepted + rejected} entries: {accepted} accepted, {rejected} rejected, {len(still_pending)} still pending")

    return {
        "total": len(entries),
        "accepted": accepted,
        "rejected": rejected,
        "still_pending": len(still_pending),
    }


def sync_feedback_to_server():
    """POST resolved feedback entries as Signals to the Maiko API.

    Accepted → signal with severity="suggestion" (model was right, user fixed it)
    Rejected → signal with severity="rejected" (model was wrong, user bypassed)

    Returns:
        dict with {synced, errors}
    """
    path = _pending_path()
    if not os.path.exists(path):
        return {"synced": 0, "errors": 0}

    with open(path, "r", encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]

    synced = 0
    errors = 0
    remaining = []

    for entry in entries:
        status = entry.get("status")
        if status not in ("accepted", "rejected"):
            remaining.append(entry)
            continue

        if entry.get("synced"):
            remaining.append(entry)
            continue

        # Build signal data
        signal_data = {
            "category": _extract_category(entry.get("model_output", "")),
            "text": entry.get("model_output", "LoRA hook feedback"),
            "source_type": "lora_hook",
            "severity": "suggestion" if status == "accepted" else "rejected",
            "repo": entry.get("repo"),
            "file_path": entry.get("file_path"),
            "code_context": entry.get("diff", "")[:2000],
        }

        try:
            from planet_maiko.database import db
            from planet_maiko.models.signal import Signal

            signal = Signal(**signal_data)
            db.session.add(signal)
            db.session.commit()

            entry["synced"] = True
            synced += 1
        except Exception as e:
            logger.warning(f"[feedback] Failed to sync entry: {e}")
            errors += 1

        remaining.append(entry)

    # Rewrite file
    with open(path, "w", encoding="utf-8") as f:
        for e in remaining:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    logger.info(f"[feedback] Synced {synced} signals ({errors} errors)")
    return {"synced": synced, "errors": errors}


def _extract_category(model_output):
    """Extract category from model output like 'VIOLATION: [security] ...'"""
    if "[" in model_output and "]" in model_output:
        start = model_output.index("[") + 1
        end = model_output.index("]")
        category = model_output[start:end].strip()
        valid = {"security", "error_handling", "testing", "performance", "api_design",
                 "architecture", "null_safety", "style", "naming", "docs", "pattern",
                 "domain_knowledge", "gotcha", "team"}
        if category in valid:
            return category
    return "pattern"
