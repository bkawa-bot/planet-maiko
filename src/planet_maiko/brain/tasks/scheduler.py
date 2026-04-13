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

# In-memory override set by the user via the Focus "regenerate with hint"
# flow. Cleared on server restart — that's intentional (see issue tracker).
_OVERRIDE_TTL_HOURS = 4
_override = None  # {"instructions": str, "ordered_task_ids": [str], "timestamp": datetime}


def set_override(instructions, ordered_task_ids):
    """Store a user-directed task ordering so compute_schedule applies it."""
    global _override
    _override = {
        "instructions": instructions,
        "ordered_task_ids": list(ordered_task_ids),
        "timestamp": datetime.now(timezone.utc),
    }


def clear_override():
    global _override
    _override = None


def get_override():
    """Return the active override, or None if missing / expired."""
    global _override
    if _override is None:
        return None
    age = datetime.now(timezone.utc) - _override["timestamp"]
    if age.total_seconds() > _OVERRIDE_TTL_HOURS * 3600:
        _override = None
        return None
    return _override


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


def _task_to_dict(t, score=0):
    return {
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


def compute_schedule():
    """Compute an optimal task schedule.

    Returns:
        dict with ordered blocks and total estimate
    """
    active_tasks = Task.query.filter(Task.status.in_(["new", "in_progress"])).all()

    if not active_tasks:
        return {"blocks": [], "total_hours": 0, "task_count": 0}

    # If the user has asked for a custom focus ordering, apply it instead
    # of the deterministic scoring. Tasks not in the override (newly created
    # since it was set) are appended at the end so they don't disappear.
    override = get_override()
    if override:
        by_id = {t.id: t for t in active_tasks}
        ordered = [by_id[tid] for tid in override["ordered_task_ids"] if tid in by_id]
        for t in active_tasks:
            if t.id not in override["ordered_task_ids"]:
                ordered.append(t)
        block = {
            "repo": "(custom focus)",
            "max_score": 999,
            "total_score": 999,
            "estimated_hours": len(ordered) * 0.5,
            "tasks": [_task_to_dict(t) for t in ordered],
        }
        return {
            "blocks": [block],
            "total_hours": block["estimated_hours"],
            "task_count": len(active_tasks),
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "override": {
                "instructions": override["instructions"],
                "applied_at": override["timestamp"].isoformat(),
            },
        }

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
