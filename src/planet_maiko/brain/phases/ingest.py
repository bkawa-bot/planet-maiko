"""Phases 1–3.5: ingest + early matching.

  - agents: process agent pupdates (auto-complete tasks, etc.)
  - auto_complete_reviews: close review tasks for approved/merged PRs
  - awareness: A2A conflict detection + resolution between active agents
  - automations: evaluate user-editable when/then rows
  - pupdates: process remaining pupdates through rules + triage
"""

import logging

logger = logging.getLogger(__name__)


def _phase_agents():
    """Phase 1: Process agent pupdates first (auto-complete tasks, etc.)."""
    from planet_maiko.agents.monitor import process_agent_pupdates
    return process_agent_pupdates()


def _phase_auto_complete_reviews():
    """Phase 1.5: Auto-complete review tasks when PRs are approved/merged."""
    try:
        from planet_maiko.models.pupdate import Pupdate
        from planet_maiko.models.task import Task
        from planet_maiko.database import db

        approved_prs = Pupdate.query.filter(
            Pupdate.type.in_(["pr_approved", "pr_merged"]),
            Pupdate.brain_processed == False,  # noqa: E712
        ).all()

        completed_count = 0
        for p in approved_prs:
            repo = (p.extra or {}).get("repo", "")
            pr_number = (p.extra or {}).get("number", "")
            if not (repo and pr_number):
                continue
            review_tasks = Task.query.filter(
                Task.type == "review",
                Task.status.in_(["new", "in_progress"]),
            ).all()
            for task in review_tasks:
                task_repo = (task.extra or {}).get("repo", "")
                task_number = str((task.extra or {}).get("number", ""))
                # Match by repo+number or by URL containing the PR
                if (task_repo == repo and task_number == str(pr_number)) or \
                   (task.url and f"/{pr_number}" in task.url and repo.split("/")[-1] in (task.url or "")):
                    task.status = "done"
                    completed_count += 1

        if completed_count:
            db.session.commit()
        return {"completed": completed_count}
    except Exception as e:
        logger.warning(f"[cycle] Review auto-complete skipped: {e}")
        return {"completed": 0}


def _phase_awareness():
    """Phase 2: Check for conflicts between active agents + attempt A2A resolution.

    Population is just Maiko-prepared worktrees (from list_prepared()).
    External-orchestrator session registration was removed; the only
    sessions Maiko knows about now are the ones it spawned itself.
    """
    try:
        from planet_maiko.brain.awareness.conflicts import detect_conflicts, resolve_conflicts
        from planet_maiko.agents.runtime import list_prepared

        prepared = list_prepared()
        # Conflicts detection keys its internal worktree dicts by
        # `task_id`. Pull `job_id` out of list_prepared and feed it
        # under the `task_id` key so detect.py keeps working.
        worktrees = [
            {"task_id": a.get("job_id", ""), "worktree_path": a.get("working_path", "")}
            for a in prepared if a.get("working_path")
        ]

        if len(worktrees) < 2:
            return {"conflicts": 0, "resolved": 0, "escalated": 0}

        conflicts = detect_conflicts(worktrees)
        if not conflicts:
            return {"conflicts": 0, "resolved": 0, "escalated": 0}

        resolution = resolve_conflicts(conflicts)
        return {"conflicts": len(conflicts), **resolution}
    except Exception as e:
        logger.warning(f"[cycle] Awareness check skipped: {e}")
        return {"conflicts": 0, "warnings_sent": 0}


def _phase_automations():
    """Phase 3.2: Unified Automation engine. Evaluates every active
    when/then row and fires actions.

    The engine reads from the Automation table; seeded + user-created
    watches all run through here. Runs before pupdate processing so
    emitted proposals get indexed the same cycle.
    """
    try:
        from planet_maiko.brain.automations import evaluate
        return evaluate()
    except Exception as e:
        logger.warning(f"[cycle] Automations phase skipped: {e}")
        return {"fired": 0, "error": str(e)}


def _phase_pupdates():
    """Phase 3.5: Process remaining pupdates through rules + triage."""
    from planet_maiko.brain.pupdates.processor import process as process_pupdates
    return process_pupdates()
