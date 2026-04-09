"""Suggestions scanner - proactively finds improvement opportunities.

Two-tier scanning:
    Daily quick scan (no LLM): stuck PRs, review bottlenecks, stale tasks
    Deep brainstorm (LLM): error trends, backlog health

Suggestions are stored as pupdates with type="suggestion" and
presented in the dashboard for the user to act on or dismiss.
"""

import json
import logging
import subprocess
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

# Stuck-task thresholds (days). After STUCK_TASK_DAYS without updates, an
# in-progress task is suggested. After STUCK_TASK_HIGH_DAYS, the suggestion
# is escalated to high priority. STALE_TASK_DAYS is how long a "new" task
# can sit before we suggest starting or cancelling.
STUCK_TASK_DAYS = 3
STUCK_TASK_HIGH_DAYS = 7
STALE_TASK_DAYS = 5


def quick_scan(repos=None):
    """Run a quick scan for common issues (no LLM needed).

    Checks:
        - PRs open >3 days with no review
        - PRs open >7 days (stuck)
        - Tasks stuck in "in_progress" for >3 days
        - Tasks with no activity for >5 days

    Returns:
        dict with suggestions created
    """
    suggestions = []

    # Stuck tasks
    suggestions.extend(_scan_stuck_tasks())

    # Stuck PRs (requires gh CLI and repos config)
    if repos:
        for repo in repos:
            suggestions.extend(_scan_stuck_prs(repo))

    # Write suggestions as pupdates
    created = 0
    for s in suggestions:
        existing = Pupdate.query.filter_by(source_id=s["source_id"]).first()
        if existing:
            continue

        pupdate = Pupdate(
            id=s["id"][:64],
            source="maiko",
            source_id=s["source_id"],
            type="suggestion",
            priority=s.get("priority", "normal"),
            title=s["title"],
            body=s.get("body", ""),
            actionable=True,
            action_hint=s.get("action_hint", "Review suggestion"),
            tags=s.get("tags", ["suggestion"]),
            extra={
                "category": s.get("category"),
                "estimated_effort": s.get("effort", "small"),
            },
        )
        db.session.add(pupdate)
        created += 1

    if created:
        db.session.commit()
        logger.info(f"[suggestions] Created {created} suggestion(s)")

    return {"scanned": True, "suggestions_created": created}


def _scan_stuck_tasks():
    """Find tasks stuck in progress or stale."""
    suggestions = []
    now = datetime.now(timezone.utc)

    in_progress = Task.query.filter_by(status="in_progress").all()
    for t in in_progress:
        if t.updated_at:
            updated = t.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            days_stuck = (now - updated).days
            if days_stuck >= STUCK_TASK_DAYS:
                suggestions.append({
                    "id": f"sug-stuck-task-{t.id}",
                    "source_id": f"maiko/suggestion/stuck_task/{t.id}",
                    "category": "stuck_task",
                    "title": f"Task stuck: {t.title} ({days_stuck}d in progress)",
                    "body": f"This task has been in progress for {days_stuck} days without updates.",
                    "action_hint": "Check on task",
                    "priority": "normal" if days_stuck < STUCK_TASK_HIGH_DAYS else "high",
                    "effort": "small",
                    "tags": ["suggestion", t.id],
                })

    stale_new = Task.query.filter_by(status="new").all()
    for t in stale_new:
        if t.created_at:
            created = t.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            days_old = (now - created).days
            if days_old >= STALE_TASK_DAYS:
                suggestions.append({
                    "id": f"sug-stale-task-{t.id}",
                    "source_id": f"maiko/suggestion/stale_task/{t.id}",
                    "category": "stale_task",
                    "title": f"Stale task: {t.title} ({days_old}d untouched)",
                    "body": f"This task was created {days_old} days ago and never started.",
                    "action_hint": "Start or cancel",
                    "priority": "low",
                    "effort": "small",
                    "tags": ["suggestion", t.id],
                })

    return suggestions


def _scan_stuck_prs(repo):
    """Find PRs that are stuck (no review, old)."""
    suggestions = []

    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--state", "open",
             "--json", "number,title,author,createdAt,reviewDecision,url"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return suggestions

        prs = json.loads(result.stdout) if result.stdout.strip() else []
        now = datetime.now(timezone.utc)

        for pr in prs:
            created = pr.get("createdAt", "")
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                days_open = (now - created_dt).days
            except (ValueError, TypeError):
                continue

            author = pr.get("author", {}).get("login", "unknown")
            number = pr.get("number")
            review = pr.get("reviewDecision", "")

            if days_open >= 7:
                suggestions.append({
                    "id": f"sug-stuck-pr-{repo}-{number}",
                    "source_id": f"maiko/suggestion/stuck_pr/{repo}#{number}",
                    "category": "stuck_pr",
                    "title": f"PR stuck: {repo}#{number} by {author} ({days_open}d)",
                    "body": f"{pr.get('title', '')}\nOpen for {days_open} days. Review status: {review or 'none'}",
                    "action_hint": "Check on PR",
                    "priority": "normal",
                    "effort": "small",
                    "tags": ["suggestion", repo],
                })
            elif days_open >= 3 and not review:
                suggestions.append({
                    "id": f"sug-no-review-{repo}-{number}",
                    "source_id": f"maiko/suggestion/no_review/{repo}#{number}",
                    "category": "review_bottleneck",
                    "title": f"No review: {repo}#{number} by {author} ({days_open}d)",
                    "body": f"{pr.get('title', '')}\nNo review after {days_open} days.",
                    "action_hint": "Request review",
                    "priority": "low",
                    "effort": "small",
                    "tags": ["suggestion", repo],
                })

    except Exception as e:
        logger.warning(f"[suggestions] Failed to scan PRs for {repo}: {e}")

    return suggestions
