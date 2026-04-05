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

    Returns: count of signals created
    """
    config = load_config()
    repos = config.get("github", {}).get("repos", [])

    if not repos:
        logger.warning("[bootstrap] No repos configured")
        return 0

    created = 0
    for repo in repos:
        try:
            # Get recent merged PRs
            result = subprocess.run(
                ["gh", "pr", "list", "--repo", repo, "--state", "merged",
                 "--limit", str(limit), "--json", "number,title,reviews"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning(f"[bootstrap] Failed to list PRs for {repo}: {result.stderr[:100]}")
                continue

            prs = json.loads(result.stdout)

            for pr in prs:
                for review in (pr.get("reviews") or []):
                    body = review.get("body", "").strip()
                    if not body or len(body) < 30:
                        continue

                    # Skip low-quality comments (approvals, short acks, emoji-only)
                    lower = body.lower()
                    skip_phrases = [
                        "lgtm", "looks good", "ship it", "approved", "nice work",
                        "thanks", "thank you", "+1", "nit:", "nit", "merge",
                    ]
                    if any(lower.strip().startswith(p) for p in skip_phrases) and len(body) < 60:
                        continue

                    # Skip if it's just a question with no actionable feedback
                    if body.strip().endswith("?") and len(body) < 80:
                        continue

                    # Dedup: skip if we already have a signal with this exact text
                    existing = Signal.query.filter_by(
                        text=body[:500], repo=repo, source_type="pr_comment"
                    ).first()
                    if existing:
                        continue

                    signal = Signal(
                        category="pattern",  # Will be classified by LLM later
                        text=body[:500],
                        source_type="pr_comment",
                        reviewer=review.get("author", {}).get("login", ""),
                        severity="suggestion",
                        repo=repo,
                    )
                    db.session.add(signal)
                    created += 1

        except subprocess.TimeoutExpired:
            logger.warning(f"[bootstrap] Timeout scanning {repo}")
        except Exception as e:
            logger.warning(f"[bootstrap] Error scanning {repo}: {e}")

    if created:
        db.session.commit()
        logger.info(f"[bootstrap] Created {created} signals from {len(repos)} repo(s)")

    return created
