"""Bootstrap learning engine from historical PR reviews."""

import logging
import subprocess
import json
from datetime import datetime, timezone
from planet_maiko.database import db
from planet_maiko.models.signal import Signal
from planet_maiko.config import load_config

logger = logging.getLogger(__name__)


def bootstrap_from_prs(limit=20):
    """Scan recent merged PRs and extract review comments as signals.

    Uses gh CLI to fetch PR review comments. Each comment becomes a signal
    with 0.5x confidence weight (historical, not live).

    Returns:
        dict with: total_created, per_repo (list of {repo, prs_scanned,
                   signals_created, error})
    """
    config = load_config()
    repos = config.get("github", {}).get("repos", [])

    if not repos:
        logger.warning("[bootstrap] No repos configured")
        return {"total_created": 0, "per_repo": []}

    total_created = 0
    per_repo = []

    for repo in repos:
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
            created_for_repo = 0

            for pr in prs:
                for review in (pr.get("reviews") or []):
                    body = review.get("body", "").strip()
                    if not body or len(body) < 30:
                        continue

                    lower = body.lower()
                    skip_phrases = [
                        "lgtm", "looks good", "ship it", "approved", "nice work",
                        "thanks", "thank you", "+1", "nit:", "nit", "merge",
                    ]
                    if any(lower.strip().startswith(p) for p in skip_phrases) and len(body) < 60:
                        continue

                    if body.strip().endswith("?") and len(body) < 80:
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

    if total_created:
        db.session.commit()
        logger.info(f"[bootstrap] Created {total_created} signals from {len(repos)} repo(s)")

    return {"total_created": total_created, "per_repo": per_repo}
