"""Shared cross-phase helpers.

Lives here (rather than in a single phase file) so any phase can
import without setting up a circular dependency with cycle.py.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _emit_missing_clone_pupdate(task, repo):
    """Surface "I can't find a local clone for this repo" as an
    actionable pupdate, dedup'd by repo so the user gets one entry
    per missing repo, not one per stuck task per cycle.

    Without this, review tasks for repos missing from
    config.github.repo_roots silently sit unrouted forever — agent is
    assigned, no worktree ever appears, AgentsActiveTab stays empty,
    and the user has no signal anything is wrong.
    """
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.database import db

    if not repo:
        repo = "(unknown repo)"

    source_id = f"missing-clone/{repo}"
    existing = Pupdate.query.filter_by(source_id=source_id).first()
    if existing and not existing.dismissed:
        # Already surfaced this repo; don't pile up duplicates.
        return

    if existing and existing.dismissed:
        # User dismissed but the problem is back — resurrect.
        existing.dismissed = False
        existing.dismissed_at = None
        existing.timestamp = datetime.now(timezone.utc)
        existing.body = (
            f"Maiko routed a {task.type} task ({task.id}) to an agent but "
            f"can't find a local clone of {repo} on disk. Add the parent "
            f"directory to Settings → GitHub → Repo Roots so worktrees "
            f"can be created."
        )
        existing.tags = list(set((existing.tags or []) + [repo, task.id]))
        db.session.flush()
        return

    p = Pupdate(
        id=f"missing-clone-{repo.replace('/', '-')}"[:64],
        source="maiko",
        source_id=source_id,
        type="missing_local_clone",
        priority="high",
        title=f"Can't find a local clone for {repo}",
        body=(
            f"Maiko routed a {task.type} task ({task.id}) to an agent but "
            f"can't find a local clone of {repo} on disk. Add the parent "
            f"directory to Settings → GitHub → Repo Roots so worktrees "
            f"can be created."
        ),
        actionable=True,
        action_hint="Open Settings",
        tags=[repo, task.id, "missing_clone"],
        extra={"repo": repo, "task_id": task.id},
    )
    db.session.add(p)
    db.session.flush()
