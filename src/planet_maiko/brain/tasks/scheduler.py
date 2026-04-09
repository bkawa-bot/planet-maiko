"""Task scheduler - orders tasks to minimize context switching.

Groups tasks by repo, scores by priority + deadline + status,
and produces a suggested work order.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)

PRIORITY_SCORES = {"urgent": 40, "high": 30, "normal": 20, "low": 10}


def _score_task(task):
    """Score a task for scheduling."""
    score = PRIORITY_SCORES.get(task.priority, 20)

    # Status bonuses
    if task.status == "in_progress":
        score += 15
    elif task.status == "new":
        score += 5

    # Deadline bonuses
    extra = task.extra or {}
    deadline = extra.get("deadline") or extra.get("due_date")
    if deadline:
        try:
            due = datetime.fromisoformat(deadline)
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if due < now:
                score += 50  # Overdue
            elif due - now < timedelta(days=2):
                score += 30  # Due soon
        except (ValueError, TypeError):
            pass

    # Pinned bonus
    if extra.get("pinned"):
        score += 200

    return score


def compute_schedule():
    """Compute an optimal task schedule.

    Returns:
        dict with ordered blocks and total estimate
    """
    active_tasks = Task.query.filter(Task.status.in_(["new", "in_progress"])).all()

    if not active_tasks:
        return {"blocks": [], "total_hours": 0, "task_count": 0}

    # Group by repo
    by_repo = defaultdict(list)
    for t in active_tasks:
        repo = (t.extra or {}).get("repo", "_unassigned")
        by_repo[repo].append(t)

    # Score and sort within each group
    blocks = []
    for repo, tasks in by_repo.items():
        scored = [(t, _score_task(t)) for t in tasks]
        scored.sort(key=lambda x: -x[1])

        max_score = scored[0][1]
        total_score = sum(s for _, s in scored)

        blocks.append({
            "repo": repo,
            "max_score": max_score,
            "total_score": total_score,
            "estimated_hours": len(tasks) * 0.5,
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "priority": t.priority,
                    "status": t.status,
                    "score": score,
                    "type": t.type,
                    "url": t.url,
                    "due_date": t.due_date,
                    "extra": t.extra or {},
                }
                for t, score in scored
            ],
        })

    # Sort blocks by max score
    blocks.sort(key=lambda b: -b["max_score"])

    total_hours = sum(b["estimated_hours"] for b in blocks)

    return {
        "blocks": blocks,
        "total_hours": total_hours,
        "task_count": len(active_tasks),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
