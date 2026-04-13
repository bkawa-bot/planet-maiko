"""Brain cycle - the clock tick that drives all processors.

Each cycle runs all phases in order, just like a CPU executes its
pipeline on each clock tick. Each phase is its own function so failures
are isolated and the orchestrator stays readable.

Pipeline (phases run in this order):
    1.  agents               — process agent pupdates (auto-complete tasks)
    1.5 auto_complete_reviews — close review tasks for approved/merged PRs
    2.  awareness            — A2A conflict detection + resolution
    2.5 calendar_focus       — auto-focus from calendar events
    3.  correlator           — group related pupdates into incidents
    3.5 pupdates             — match remaining pupdates against rules
    3.6 llm_triage           — Tier 2 LLM triage for unmatched pupdates
    4.  learning             — aggregate signals into learnings
    4.5 classification       — batch classify unclassified PR feedback
    5.  heartbeats           — nudge silent agents
    6.  projects             — auto-advance project phases
    7.  scheduled_skills     — run skills on their schedules
    8.  orchestrate          — materialize investigation tasks + route
                               unassigned tasks to agent profiles
    8b. unblock              — cascade depends_on completion
    8c. execute_agent_tasks  — run review / investigation agents' tasks

Note: morning brief is user-triggered from the Home page (not a cycle
phase — nobody wants a "morning" brief running at 3am when the first
cycle happens to tick). Brainstorm can be set up as a scheduled skill
via the Skills page if the user wants it recurring.
"""

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Track cycle history for status reporting
_last_cycle = None
_cycle_count = 0

_status_cache = None
_status_cache_at = 0


# ---------------------------------------------------------------------------
# Phase functions — each returns its result dict (never raises)
# ---------------------------------------------------------------------------

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
        logger.debug(f"[cycle] Review auto-complete skipped: {e}")
        return {"completed": 0}


def _phase_awareness():
    """Phase 2: Check for conflicts between active agents + attempt A2A resolution."""
    try:
        from planet_maiko.brain.awareness.conflicts import detect_conflicts, resolve_conflicts
        from planet_maiko.agents.coding_agent import list_prepared

        prepared = list_prepared()
        worktrees = [
            {"task_id": a.get("task_id", ""), "worktree_path": a.get("working_path", "")}
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
        logger.debug(f"[cycle] Awareness check skipped: {e}")
        return {"conflicts": 0, "warnings_sent": 0}


def _phase_calendar_focus():
    """Phase 2.5: Auto-focus from calendar events."""
    try:
        from planet_maiko.brain.focus.manager import check_calendar_focus
        from planet_maiko.models.pupdate import Pupdate
        recent = Pupdate.query.filter(Pupdate.brain_processed == False).all()  # noqa: E712
        return {"changed": check_calendar_focus(recent)}
    except Exception as e:
        logger.debug(f"[cycle] Calendar focus check skipped: {e}")
        return {"changed": False}


def _phase_correlator():
    """Phase 3: Correlate related pupdates into incidents."""
    from planet_maiko.brain.pupdates.correlator import correlate
    return correlate()


def _phase_pupdates():
    """Phase 3.5: Process remaining pupdates through rules + triage."""
    from planet_maiko.brain.pupdates.processor import process as process_pupdates
    return process_pupdates()


def _phase_llm_triage():
    """Phase 3.6: Tier 2 LLM triage for unmatched pupdates."""
    try:
        from planet_maiko.config import load_config
        config = load_config()
        if not config.get("brain", {}).get("llm_triage", False):
            return {"processed": 0, "skipped": "disabled"}

        from planet_maiko.models.pupdate import Pupdate
        unmatched = Pupdate.query.filter(
            Pupdate.brain_processed == False,  # noqa: E712
            Pupdate.dismissed == False,  # noqa: E712
            Pupdate.read == False,  # noqa: E712
        ).limit(5).all()
        if not unmatched:
            return {"processed": 0}

        from planet_maiko.agents.brain_session import _get_runtime, triage_pupdate
        runtime = _get_runtime()
        if not (runtime and runtime.is_available()):
            return {"processed": 0, "skipped": "no_runtime"}

        from planet_maiko.database import db
        from planet_maiko.models.task import Task
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
        return {"processed": len(unmatched)}
    except Exception as e:
        logger.warning(f"[cycle] LLM triage phase error: {e}")
        return {"processed": 0, "error": str(e)}


def _phase_learning():
    """Phase 4: Aggregate feedback signals into learnings."""
    from planet_maiko.brain.learning.processor import process_signals
    return process_signals()


def _phase_classification():
    """Phase 4.5: Batch classify unclassified signals + pattern learnings."""
    try:
        from planet_maiko.brain.learning.classifier import (
            classify_unclassified_signals, classify_pattern_learnings
        )
        classified = classify_unclassified_signals(batch_size=20)
        reclassified = classify_pattern_learnings(batch_size=20)
        return {
            "classified_signals": classified,
            "classified_learnings": reclassified,
        }
    except Exception as e:
        logger.warning(f"[cycle] Classification error: {e}")
        return {"classified_signals": 0, "classified_learnings": 0, "error": str(e)}


def _phase_heartbeats():
    """Phase 5: Heartbeats — nudge silent agents."""
    try:
        from planet_maiko.agents.monitor import check_heartbeats
        return {"nudged": check_heartbeats()}
    except Exception as e:
        logger.warning(f"[cycle] Heartbeat check error: {e}")
        return {"nudged": 0, "error": str(e)}


def _phase_projects():
    """Phase 6: Project driver — auto-advance project phases."""
    try:
        from planet_maiko.brain.projects.driver import drive_projects
        return drive_projects()
    except Exception as e:
        logger.warning(f"[cycle] Project driver error: {e}")
        return {"advanced": 0, "completed": 0, "error": str(e)}


def _phase_scheduled_skills():
    """Phase 7: Run skills on their schedules."""
    try:
        from planet_maiko.pollers.skill_runner import run_scheduled_skills
        return run_scheduled_skills()
    except Exception as e:
        logger.warning(f"[cycle] Skill runner error: {e}")
        return []


_INVESTIGATION_TYPES = ("pr_ci_failed", "incident", "error_spike")


def _phase_orchestrate():
    """Phase 8: Route unassigned tasks to agents and create tasks for
    investigable signals that haven't been materialized yet.

    Does three things per cycle:

    1. For each brain-processed investigable pupdate (CI failure, incident,
       error spike) without a spawned investigation task, create one.
    2. For every task with status in (new, blocked) and no
       assigned_agent_id, call route(task) to assign.
    3. Keep review tasks pointed at a review-role agent even if the
       original task_from_review_request rule assigned them generically.

    Idempotent — repeated runs are no-ops once everything is routed.
    """
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.task import Task
    from planet_maiko.database import db
    from planet_maiko.orchestration import route

    created = 0
    routed = 0

    try:
        # 1. Materialize investigation tasks for unprocessed signals.
        to_investigate = Pupdate.query.filter(
            Pupdate.type.in_(_INVESTIGATION_TYPES),
            Pupdate.brain_processed == True,  # noqa: E712
            Pupdate.dismissed == False,  # noqa: E712
            ~Pupdate.tags.contains("investigation_spawned"),
        ).limit(4).all()

        for p in to_investigate:
            meta = p.extra or {}
            repo = meta.get("repo") or meta.get("repository")
            task_id = f"investigation-{p.id[:24]}"
            if db.session.get(Task, task_id):
                p.tags = list(p.tags or []) + ["investigation_spawned"]
                continue
            t = Task(
                id=task_id,
                title=f"Investigate: {p.title}",
                type="investigation",
                status="new",
                priority=p.priority or "normal",
                source_pupdate_id=p.id,
                url=p.url,
                extra={"repo": repo} if repo else {},
                tags=["investigation"],
            )
            db.session.add(t)
            p.tags = list(p.tags or []) + ["investigation_spawned"]
            created += 1

        # 2. Route any still-unrouted active tasks.
        unrouted = Task.query.filter(
            Task.status.in_(["new", "blocked"]),
            Task.assigned_agent_id.is_(None),
        ).limit(50).all()
        for t in unrouted:
            try:
                route(t)
                routed += 1
            except Exception as e:
                logger.debug(f"[cycle] route() failed for {t.id}: {e}")

        if created or routed:
            db.session.commit()
        return {"investigation_tasks_created": created, "routed": routed}
    except Exception as e:
        logger.debug(f"[cycle] Orchestrate skipped: {e}")
        return {"investigation_tasks_created": 0, "routed": 0, "error": str(e)}


def _phase_unblock_tasks():
    """Phase 8b: Cascade dep completion — any blocked task whose deps are
    all done flips to "new" so its agent can pick it up."""
    from planet_maiko.models.task import Task
    from planet_maiko.database import db
    from planet_maiko.orchestration import is_ready

    try:
        blocked = Task.query.filter(Task.status == "blocked").all()
        unblocked = 0
        for t in blocked:
            if is_ready(t):
                t.status = "new"
                unblocked += 1
        if unblocked:
            db.session.commit()
            logger.info(f"[cycle] Unblocked {unblocked} task(s)")
        return {"unblocked": unblocked}
    except Exception as e:
        logger.debug(f"[cycle] Unblock phase skipped: {e}")
        return {"unblocked": 0}


def _phase_stuck_escalation():
    """Phase 8d: Surface tasks stuck in_progress for too long as a
    high-priority "needs rescue" pupdate.

    A task in_progress whose updated_at is older than STUCK_DAYS gets a
    single escalation pupdate (dedup by source_id). The user can open
    the task and hit "Reassign" to route it to a different agent. When
    the task is no longer stuck (moved to done/cancelled or updated),
    the escalation pupdate is auto-dismissed.
    """
    from datetime import datetime, timezone, timedelta
    from planet_maiko.models.task import Task
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.database import db

    STUCK_DAYS = 3
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=STUCK_DAYS)

    try:
        in_progress = Task.query.filter(Task.status == "in_progress").all()
        escalated = 0
        for t in in_progress:
            updated = t.updated_at
            if updated is None:
                continue
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if updated >= threshold:
                continue

            source_id = f"stuck-task/{t.id}"
            existing = Pupdate.query.filter_by(source_id=source_id).first()
            if existing and not existing.dismissed:
                continue  # already surfaced

            days = (now - updated).days
            agent_name = (t.assigned_agent_id or "unassigned")
            p = Pupdate(
                id=f"stuck-{t.id[:32]}",
                source="maiko",
                source_id=source_id,
                type="stuck_task",
                priority="high",
                title=f"Stuck task: {t.title}",
                body=f"Task has been in_progress for {days} days with no updates. Assigned to {agent_name}.",
                actionable=True,
                action_hint="Reassign or check in",
                tags=["stuck", t.id, agent_name],
                extra={"task_id": t.id, "days_stuck": days},
            )
            db.session.add(p)
            escalated += 1

        # Auto-dismiss escalations whose task is no longer stuck.
        dismissed = 0
        open_escalations = Pupdate.query.filter_by(type="stuck_task", dismissed=False).all()
        for p in open_escalations:
            task_id = (p.extra or {}).get("task_id")
            if not task_id:
                continue
            task = db.session.get(Task, task_id)
            if not task or task.status != "in_progress":
                # Task completed / cancelled / gone — close the escalation.
                p.dismissed = True
                p.dismissed_at = now
                dismissed += 1

        if escalated or dismissed:
            db.session.commit()
        return {"escalated": escalated, "auto_dismissed": dismissed}
    except Exception as e:
        logger.debug(f"[cycle] Stuck escalation skipped: {e}")
        return {"escalated": 0, "auto_dismissed": 0}


def _phase_execute_agent_tasks():
    """Phase 8c: Run one-shot skill-based agents (review / investigation)
    on their assigned tasks.

    Coding tasks are NOT handled here — those go through coding_agent's
    worktree + session flow when the user (or an auto-start setting)
    kicks them off. Only review-role and investigation-role agents whose
    work is a single skill call get executed here.

    Limited to 2 per cycle to bound token spend. When a task completes,
    the artifact is stored on task.extra, a result pupdate is created
    for visibility in From Maiko, and the task is marked done.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.database import db
    import uuid
    from datetime import datetime, timezone

    ROLE_FOR_TYPE = {
        "investigation": ("investigation", "investigate"),
        "repo_analysis": ("investigation", "repo-analysis"),
        "review": ("review", "pr-review"),
        "pr_review": ("review", "pr-review"),
    }

    try:
        # Candidates: tasks in "new" status, assigned, with a type we
        # can execute via a single skill call.
        candidates = Task.query.filter(
            Task.status == "new",
            Task.assigned_agent_id.isnot(None),
            Task.type.in_(list(ROLE_FOR_TYPE.keys())),
        ).limit(2).all()

        if not candidates:
            return {"executed": 0}

        from planet_maiko.agents.brain_session import run_skill_as_agent

        executed = 0
        for task in candidates:
            role, skill_name = ROLE_FOR_TYPE[task.type]
            agent = db.session.get(AgentProfile, task.assigned_agent_id)
            if not agent or agent.role != role:
                # Router mis-assigned — skip, next cycle will re-route
                continue

            task.status = "in_progress"
            db.session.commit()

            meta = task.extra or {}
            context = {
                "query": task.title,
                "context": f"URL: {task.url or ''}\nRepo: {meta.get('repo', '')}",
                "pupdates": "[]", "tasks": "[]", "calendar": "[]",
            }

            try:
                result = run_skill_as_agent(
                    agent.id, skill_name, context=context,
                )
            except Exception as e:
                logger.warning(f"[cycle] Agent task {task.id} run failed: {e}")
                task.status = "new"  # allow retry next cycle
                task.extra = {**(task.extra or {}), "last_error": str(e)[:200]}
                db.session.commit()
                continue

            if not result or not result.get("success") or not result.get("output"):
                task.status = "new"
                task.extra = {**(task.extra or {}), "last_error": (result or {}).get("error", "no output")[:200]}
                db.session.commit()
                continue

            output = result["output"]
            task.status = "done"
            task.extra = {
                **(task.extra or {}),
                "artifact": output[:16000],
                "completed_by": agent.id,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }

            # Publish a pupdate so the artifact surfaces in "From Maiko"
            result_type = "pr_review_complete" if role == "review" else "investigation_complete"
            action_hint = "Open review" if role == "review" else "Open investigation"
            title_prefix = "Review ready" if role == "review" else "Investigation ready"
            result_pupdate = Pupdate(
                id=f"{role}-result-{uuid.uuid4().hex[:8]}",
                source="maiko",
                type=result_type,
                priority="normal",
                title=f"{title_prefix}: {task.title}",
                body=output[:8000],
                url=task.url,
                actionable=True,
                action_hint=action_hint,
                tags=[role, "maiko", agent.id],
            )
            db.session.add(result_pupdate)

            # Attribution — increment the agent's completed count.
            agent.tasks_completed = (agent.tasks_completed or 0) + 1
            agent.last_active_at = datetime.now(timezone.utc)

            executed += 1

        db.session.commit()
        return {"executed": executed}
    except Exception as e:
        logger.debug(f"[cycle] Execute agent tasks skipped: {e}")
        return {"executed": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# Phase order. Each entry is (result_key, phase_function). The orchestrator
# runs them in order, stores each result under its key, and fires plugin
# hooks for every phase.
_PHASES = [
    ("agents", _phase_agents),
    ("auto_complete_reviews", _phase_auto_complete_reviews),
    ("awareness", _phase_awareness),
    ("calendar_focus", _phase_calendar_focus),
    ("correlator", _phase_correlator),
    ("pupdates", _phase_pupdates),
    ("llm_triage", _phase_llm_triage),
    ("learning", _phase_learning),
    ("classification", _phase_classification),
    ("heartbeats", _phase_heartbeats),
    ("projects", _phase_projects),
    ("scheduled_skills", _phase_scheduled_skills),
    ("orchestrate", _phase_orchestrate),
    ("unblock", _phase_unblock_tasks),
    ("execute_agent_tasks", _phase_execute_agent_tasks),
    ("stuck_escalation", _phase_stuck_escalation),
]


def run(app):
    """Execute one full brain cycle.

    Args:
        app: Flask app (needed for app context)

    Returns:
        dict mapping phase name → result dict
    """
    global _last_cycle, _cycle_count

    with app.app_context():
        logger.info(f"=== Brain cycle #{_cycle_count + 1} ===")

        results = {}
        for key, phase_fn in _PHASES:
            results[key] = phase_fn()

        # Fire plugin hooks for all completed phases
        from planet_maiko.plugins.loader import fire_hook
        for phase_name, phase_results in results.items():
            fire_hook("on_brain_cycle", phase_name, phase_results, app)

        _last_cycle = datetime.now(timezone.utc)
        _cycle_count += 1

        logger.info(f"=== Cycle #{_cycle_count} complete ===")
        return results


def get_status():
    """Get brain status for the dashboard. Cached for 5 seconds."""
    global _status_cache, _status_cache_at
    if _status_cache and (time.time() - _status_cache_at) < 5:
        return _status_cache

    pending = {}
    try:
        from planet_maiko.models.pupdate import Pupdate
        from planet_maiko.models.signal import Signal
        from planet_maiko.models.learning import Learning
        pending["unprocessed_pupdates"] = Pupdate.query.filter_by(brain_processed=False, dismissed=False).count()
        pending["unclassified_signals"] = Signal.query.filter_by(category="pattern", aggregated=False).count()
        pending["pending_learnings"] = Learning.query.filter_by(status="pending").count()
    except Exception as e:
        logger.debug(f"[cycle] Status pending counts failed: {e}")

    _status_cache = {
        "last_cycle": _last_cycle.isoformat() if _last_cycle else None,
        "cycle_count": _cycle_count,
        "pending": pending,
    }
    _status_cache_at = time.time()
    return _status_cache
