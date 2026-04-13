"""Bootstrap learning engine from historical PR reviews."""

import logging
import subprocess
import json
import threading
from datetime import datetime, timezone
from planet_maiko.database import db
from planet_maiko.models.signal import Signal
from planet_maiko.config import load_config

logger = logging.getLogger(__name__)

# Pre-LLM filters have been removed — the synthesis step is better at
# separating signal from noise than brittle length / phrase heuristics
# were. Only empty bodies are skipped here.


# Progress state for the async backfill job. Accessed through the helpers
# below so we have a single place to update it and a single lock guarding
# the dict. The UI polls get_backfill_progress() to render a progress bar.
_progress_lock = threading.Lock()
_progress = {
    "running": False,
    "phase": "idle",          # idle | fetching | synthesizing | aggregating | done | error
    "repos_total": 0,
    "repos_done": 0,
    "current_repo": None,
    "prs_in_repo": 0,
    "signals_created": 0,
    "synthesized": 0,
    "new_learnings": 0,
    "graduated": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "result": None,           # full result dict once phase == done
}


def get_backfill_progress():
    with _progress_lock:
        return dict(_progress)


def update_backfill_progress(**kwargs):
    with _progress_lock:
        _progress.update(kwargs)


def reset_backfill_progress():
    with _progress_lock:
        _progress.update({
            "running": False, "phase": "idle", "repos_total": 0, "repos_done": 0,
            "current_repo": None, "prs_in_repo": 0, "signals_created": 0,
            "synthesized": 0, "new_learnings": 0, "graduated": 0, "error": None,
            "started_at": None, "finished_at": None, "result": None,
        })


def bootstrap_from_prs(limit=20, repos=None):
    """Scan recent merged PRs and extract review comments as signals.

    Uses gh CLI to fetch PR review comments. Each comment becomes a signal
    with 0.5x confidence weight (historical, not live).

    Args:
        limit: max PRs to scan per repo
        repos: list of repos to scan (None = all configured repos)

    Returns:
        dict with: total_created, per_repo (list of {repo, prs_scanned,
                   signals_created, error})
    """
    if repos is None:
        config = load_config()
        repos = config.get("github", {}).get("repos", [])

    if not repos:
        logger.warning("[bootstrap] No repos configured or specified")
        return {"total_created": 0, "per_repo": []}

    total_created = 0
    per_repo = []
    update_backfill_progress(repos_total=len(repos), repos_done=0)

    for repo in repos:
        update_backfill_progress(current_repo=repo, prs_in_repo=0)
        repo_stats = {"repo": repo, "prs_scanned": 0, "signals_created": 0, "error": None}

        try:
            result = subprocess.run(
                ["gh", "pr", "list", "--repo", repo, "--state", "merged",
                 "--limit", str(limit), "--json", "number,title,reviews"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                err = result.stderr.strip()[:200]
                repo_stats["error"] = err or f"gh exited {result.returncode}"
                logger.warning(f"[bootstrap] Failed to list PRs for {repo}: {err}")
                per_repo.append(repo_stats)
                continue

            prs = json.loads(result.stdout)
            repo_stats["prs_scanned"] = len(prs)
            update_backfill_progress(prs_in_repo=len(prs))
            created_for_repo = 0

            for pr in prs:
                for review in (pr.get("reviews") or []):
                    body = review.get("body", "").strip()
                    if not body:
                        continue

                    existing = Signal.query.filter_by(
                        text=body[:500], repo=repo, source_type="pr_comment"
                    ).first()
                    if existing:
                        continue

                    signal = Signal(
                        category="pattern",
                        text=body[:500],
                        source_type="pr_comment",
                        reviewer=review.get("author", {}).get("login", ""),
                        severity="suggestion",
                        repo=repo,
                    )
                    db.session.add(signal)
                    created_for_repo += 1

            repo_stats["signals_created"] = created_for_repo
            total_created += created_for_repo

        except subprocess.TimeoutExpired:
            repo_stats["error"] = "timeout"
            logger.warning(f"[bootstrap] Timeout scanning {repo}")
        except Exception as e:
            repo_stats["error"] = str(e)[:200]
            logger.warning(f"[bootstrap] Error scanning {repo}: {e}")

        per_repo.append(repo_stats)
        update_backfill_progress(
            repos_done=len(per_repo),
            signals_created=total_created,
        )

    if total_created:
        db.session.commit()
        logger.info(f"[bootstrap] Created {total_created} signals from {len(repos)} repo(s)")

    return {"total_created": total_created, "per_repo": per_repo}
