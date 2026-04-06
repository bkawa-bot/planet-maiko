"""Brain cycle - the clock tick that drives all processors.

Each cycle runs all registered processors in order,
just like a CPU executes its pipeline on each clock tick.

Processor pipeline:
    1. agents:        process agent pupdates (auto-complete tasks)
    2. awareness:     detect conflicts between active agents (A2A)
    3. correlator:    group related pupdates into incidents
    4. pupdates:      match pupdates against rules + triage
    4.5 classifier:   batch classify unclassified PR feedback signals
    5. learning:      aggregate signals into learnings
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Track cycle history for status reporting
_last_cycle = None
_cycle_count = 0


def run(app):
    """Execute one full brain cycle.

    Args:
        app: Flask app (needed for app context)

    Returns:
        dict with results from each processor
    """
    global _last_cycle, _cycle_count

    with app.app_context():
        logger.info(f"=== Brain cycle #{_cycle_count + 1} ===")

        results = {}

        # Phase 1: Process agent pupdates first (auto-complete tasks, etc.)
        from planet_maiko.agents.monitor import process_agent_pupdates
        results["agents"] = process_agent_pupdates()

        # Phase 1.5: Auto-complete review tasks when PRs are approved/merged
        try:
            from planet_maiko.models.pupdate import Pupdate as ReviewPupdate
            from planet_maiko.models.task import Task as ReviewTask
            from planet_maiko.database import db as review_db

            approved_prs = ReviewPupdate.query.filter(
                ReviewPupdate.type.in_(["pr_approved", "pr_merged"]),
                ReviewPupdate.brain_processed == False,
            ).all()

            completed_count = 0
            for p in approved_prs:
                repo = (p.extra or {}).get("repo", "")
                pr_number = (p.extra or {}).get("number", "")
                if repo and pr_number:
                    # Find matching review tasks
                    review_tasks = ReviewTask.query.filter(
                        ReviewTask.type == "review",
                        ReviewTask.status.in_(["new", "in_progress"]),
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
                review_db.session.commit()
            results["auto_complete_reviews"] = {"completed": completed_count}
        except Exception as e:
            logger.debug(f"Review auto-complete skipped: {e}")

        # Phase 2: Check for conflicts between active agents + attempt A2A resolution
        from planet_maiko.brain.awareness.conflicts import detect_conflicts, send_conflict_warnings, resolve_conflicts
        from planet_maiko.agents.coding_agent import list_prepared
        try:
            prepared = list_prepared()
            worktrees = [
                {"task_id": a.get("task_id", ""), "worktree_path": a.get("working_path", "")}
                for a in prepared if a.get("working_path")
            ]
            if len(worktrees) >= 2:
                conflicts = detect_conflicts(worktrees)
                if conflicts:
                    # Try A2A resolution first
                    resolution = resolve_conflicts(conflicts)
                    results["awareness"] = {
                        "conflicts": len(conflicts),
                        **resolution,
                    }
                else:
                    results["awareness"] = {"conflicts": 0, "resolved": 0, "escalated": 0}
            else:
                results["awareness"] = {"conflicts": 0, "resolved": 0, "escalated": 0}
        except Exception as e:
            logger.debug(f"Awareness check skipped: {e}")
            results["awareness"] = {"conflicts": 0, "warnings_sent": 0}

        # Phase 2.5: Auto-focus from calendar events
        from planet_maiko.brain.focus.manager import check_calendar_focus
        from planet_maiko.models.pupdate import Pupdate
        try:
            recent_pupdates = Pupdate.query.filter(
                Pupdate.brain_processed == False,
            ).all()
            calendar_changed = check_calendar_focus(recent_pupdates)
            results["calendar_focus"] = {"changed": calendar_changed}
        except Exception as e:
            logger.debug(f"Calendar focus check skipped: {e}")
            results["calendar_focus"] = {"changed": False}

        # Phase 3: Correlate related pupdates into incidents
        from planet_maiko.brain.pupdates.correlator import correlate
        results["correlator"] = correlate()

        # Phase 3: Process remaining pupdates through rules + triage
        from planet_maiko.brain.pupdates.processor import process as process_pupdates
        results["pupdates"] = process_pupdates()

        # Tier 2: LLM triage for unmatched pupdates
        try:
            from planet_maiko.config import load_config
            config = load_config()
            if config.get("brain", {}).get("llm_triage", False):
                unmatched = Pupdate.query.filter(
                    Pupdate.brain_processed == False,
                    Pupdate.dismissed == False,
                    Pupdate.read == False,
                ).limit(5).all()

                if unmatched:
                    from planet_maiko.agents.brain_session import _get_runtime, triage_pupdate
                    runtime = _get_runtime()
                    if runtime and runtime.is_available():
                        from planet_maiko.database import db
                        for p in unmatched:
                            try:
                                decision = triage_pupdate(p)
                                if decision:
                                    action = decision.get("action", "skip")
                                    if action == "dismiss":
                                        p.dismissed = True
                                        p.dismissed_at = datetime.now(timezone.utc)
                                    elif action == "mark_read":
                                        p.read = True
                                    elif action == "create_task":
                                        from planet_maiko.models.task import Task
                                        task = Task(
                                            id=f"task-{p.id}",
                                            title=decision.get("task_title", p.title),
                                            type=decision.get("task_type", "todo"),
                                            priority=decision.get("task_priority", p.priority),
                                            source_pupdate_id=p.id,
                                            url=p.url,
                                            tags=p.tags or [],
                                        )
                                        db.session.add(task)
                                p.brain_processed = True
                            except Exception as e:
                                logger.warning(f"[cycle] LLM triage failed for {p.id}: {e}")
                        db.session.commit()
                        results["llm_triage"] = {"processed": len(unmatched)}
        except Exception as e:
            logger.warning(f"[cycle] LLM triage phase error: {e}")

        # Phase 4: Aggregate feedback signals into learnings
        from planet_maiko.brain.learning.processor import process_signals
        results["learning"] = process_signals()

        # Phase 4.5: Batch classify unclassified signals
        try:
            from planet_maiko.brain.learning.classifier import classify_unclassified_signals
            classified = classify_unclassified_signals(batch_size=20)
            results["classification"] = {"classified": classified}
        except Exception as e:
            logger.warning(f"[cycle] Classification error: {e}")

        # Phase 5: Tournaments — auto-run on recently merged PRs
        try:
            from planet_maiko.brain.learning.tournament import run_tournament
            from planet_maiko.models.pupdate import Pupdate as TournamentPupdate

            merged_prs = TournamentPupdate.query.filter(
                TournamentPupdate.type == "pr_merged",
                TournamentPupdate.brain_processed == True,  # noqa: E712
                ~TournamentPupdate.tags.contains("tournament_run"),
            ).order_by(TournamentPupdate.timestamp.desc()).limit(3).all()

            tournament_results = {"triggered": 0, "failed": 0}
            for p in merged_prs:
                repo = p.extra.get("repo") if p.extra else None
                pr_number = p.extra.get("number") if p.extra else None
                if repo and pr_number:
                    try:
                        run_tournament(repo, int(pr_number), app)
                        # New list to avoid SQLAlchemy JSON mutation tracking issue
                        p.tags = list(p.tags or []) + ["tournament_run"]
                        tournament_results["triggered"] += 1
                    except Exception as e:
                        logger.warning(f"Tournament failed for {repo}#{pr_number}: {e}")
                        tournament_results["failed"] += 1

            if merged_prs:
                from planet_maiko.database import db as cycle_db
                cycle_db.session.commit()

            results["tournaments"] = tournament_results
        except Exception as e:
            logger.warning(f"[cycle] Tournament phase error: {e}")
            results["tournaments"] = {"triggered": 0, "failed": 0, "error": str(e)}

        # Phase 6: Heartbeats — nudge silent agents
        try:
            from planet_maiko.agents.monitor import check_heartbeats
            nudged = check_heartbeats()
            results["heartbeats"] = {"nudged": nudged}
        except Exception as e:
            logger.warning(f"[cycle] Heartbeat check error: {e}")
            results["heartbeats"] = {"nudged": 0, "error": str(e)}

        # Phase 7: Project driver — auto-advance project phases
        try:
            from planet_maiko.brain.projects.driver import drive_projects
            driver_result = drive_projects()
            results["projects"] = driver_result
        except Exception as e:
            logger.warning(f"[cycle] Project driver error: {e}")
            results["projects"] = {"advanced": 0, "completed": 0, "error": str(e)}

        # Phase 8: Scheduled skills
        try:
            from planet_maiko.pollers.skill_runner import run_scheduled_skills
            ran_skills = run_scheduled_skills()
            results["scheduled_skills"] = ran_skills
        except Exception as e:
            logger.warning(f"[cycle] Skill runner error: {e}")
            results["scheduled_skills"] = []

        # Fire plugin hooks for all completed phases
        from planet_maiko.plugins.loader import fire_hook
        for phase_name, phase_results in results.items():
            fire_hook("on_brain_cycle", phase_name, phase_results, app)

        _last_cycle = datetime.now(timezone.utc)
        _cycle_count += 1

        logger.info(f"=== Cycle #{_cycle_count} complete ===")
        return results


_status_cache = None
_status_cache_at = 0


def get_status():
    """Get brain status for the dashboard. Cached for 5 seconds."""
    import time
    global _status_cache, _status_cache_at
    if _status_cache and (time.time() - _status_cache_at) < 5:
        return _status_cache
    # Count pending items
    pending = {}
    try:
        from planet_maiko.models.pupdate import Pupdate
        from planet_maiko.models.signal import Signal
        from planet_maiko.models.learning import Learning
        pending["unprocessed_pupdates"] = Pupdate.query.filter_by(brain_processed=False, dismissed=False).count()
        pending["unclassified_signals"] = Signal.query.filter_by(category="pattern", aggregated=False).count()
        pending["pending_learnings"] = Learning.query.filter_by(status="pending").count()
    except Exception:
        pass

    _status_cache = {
        "last_cycle": _last_cycle.isoformat() if _last_cycle else None,
        "cycle_count": _cycle_count,
        "pending": pending,
    }
    _status_cache_at = time.time()
    return _status_cache
