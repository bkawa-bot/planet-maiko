"""Expertise graph - who-knows-what map built from PR history.

Scans merged PRs to build a graph of team member → repo/path expertise.
Scores decay over time so recent knowledge ranks higher.

Used for:
    - Expert routing ("who should review this PR?")
    - Annotating pupdates with relevant experts
    - Smart assignment of tasks
"""

import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

# In-memory expertise graph
_expertise = {}
_last_built = None


def build(repos, gh_user=None):
    """Build expertise graph from recent merged PRs.

    Args:
        repos: list of repo names (e.g. ["org/repo1", "org/repo2"])
        gh_user: if set, only track this user's team (optional)

    Returns:
        dict with expertise data
    """
    global _expertise, _last_built

    for repo in repos:
        try:
            # Get recent merged PRs
            result = subprocess.run(
                ["gh", "pr", "list", "--repo", repo, "--state", "merged",
                 "--limit", "50", "--json", "author,files,mergedAt,number"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning(f"[expertise] Failed to query {repo}: {result.stderr}")
                continue

            prs = json.loads(result.stdout) if result.stdout.strip() else []
            now = datetime.now(timezone.utc)

            for pr in prs:
                author = pr.get("author", {}).get("login", "unknown")
                merged_at = pr.get("mergedAt")
                files = pr.get("files", [])

                if not merged_at or not files:
                    continue

                # Parse merge date
                try:
                    merged = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue

                days_ago = (now - merged).days

                # Time-decay score: halves every 30 days
                decay = 1 / (1 + days_ago / 30)

                # Update expertise
                if author not in _expertise:
                    _expertise[author] = {}
                if repo not in _expertise[author]:
                    _expertise[author][repo] = {
                        "commit_count": 0,
                        "file_count": 0,
                        "paths": set(),
                        "last_active": merged.isoformat(),
                        "score": 0.0,
                    }

                entry = _expertise[author][repo]
                entry["commit_count"] += 1
                entry["file_count"] += len(files)
                entry["score"] += decay

                # Track top-level paths
                for f in files:
                    path = f.get("path", "") if isinstance(f, dict) else str(f)
                    parts = path.split("/")
                    if len(parts) >= 2:
                        entry["paths"].add("/".join(parts[:2]))

                # Update last_active if more recent
                if merged.isoformat() > entry["last_active"]:
                    entry["last_active"] = merged.isoformat()

        except Exception as e:
            logger.error(f"[expertise] Error scanning {repo}: {e}")

    # Convert sets to lists for JSON serialization
    for author in _expertise:
        for repo in _expertise[author]:
            paths = _expertise[author][repo].get("paths", set())
            if isinstance(paths, set):
                _expertise[author][repo]["paths"] = sorted(list(paths))[:20]

    _last_built = datetime.now(timezone.utc)
    logger.info(f"[expertise] Built graph: {len(_expertise)} contributors across {len(repos)} repos")
    return get_graph()


def get_graph():
    """Get the full expertise graph."""
    return {
        "expertise": _expertise,
        "last_built": _last_built.isoformat() if _last_built else None,
        "contributor_count": len(_expertise),
    }


def get_experts_for(repo, path_prefix=None):
    """Find experts for a specific repo/path.

    Returns:
        sorted list of {author, score, commit_count, last_active, paths}
    """
    experts = []
    now = datetime.now(timezone.utc)

    for author, repos in _expertise.items():
        if repo not in repos:
            continue

        entry = repos[repo]

        # If path_prefix specified, check if author has touched those paths
        if path_prefix:
            matching_paths = [p for p in entry.get("paths", []) if p.startswith(path_prefix)]
            if not matching_paths:
                continue

        # Recalculate score with current time decay
        try:
            last = datetime.fromisoformat(entry["last_active"])
            days_since = (now - last).days
        except (ValueError, TypeError):
            days_since = 999

        experts.append({
            "author": author,
            "score": round(entry["score"], 2),
            "commit_count": entry["commit_count"],
            "last_active": entry["last_active"],
            "days_since_last": days_since,
            "paths": entry.get("paths", []),
        })

    experts.sort(key=lambda x: -x["score"])
    return experts


def should_rebuild():
    """Check if the graph should be rebuilt (>7 days old)."""
    if _last_built is None:
        return True
    return (datetime.now(timezone.utc) - _last_built).days > 7
