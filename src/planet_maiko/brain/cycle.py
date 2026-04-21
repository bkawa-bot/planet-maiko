"""Brain cycle - the clock tick that drives all processors.

Each cycle runs all phases in order, just like a CPU executes its
pipeline on each clock tick. Each phase is its own function so failures
are isolated and the orchestrator stays readable.

Pipeline (phases run in this order):
    1.  agents               — process agent pupdates (auto-complete tasks)
    1.5 auto_complete_reviews — close review tasks for approved/merged PRs
    2.  awareness            — A2A conflict detection + resolution
    2.5 calendar_focus       — auto-focus from calendar events
    3.2 automations          — evaluate user-editable when/then rows (replaced correlator)
    3.5 pupdates             — match remaining pupdates against rules
    3.6 llm_triage           — Tier 2 LLM triage for unmatched pupdates
    4.  learning             — aggregate signals into learnings
    5.  heartbeats           — nudge silent agents
    6.  projects             — auto-advance project phases
    7.  scheduled_skills     — run skills on their schedules
    8.  orchestrate          — materialize investigation tasks + route
                               unassigned tasks to agent profiles
    8b. unblock              — cascade depends_on completion
    8b2.spawn_jobs_for_tasks — turn assigned review tasks into AgentJobs
    8c. execute_agent_jobs   — run queued AgentJobs (review + pack-owned)
    8d. execute_agent_tasks  — safety net for investigation/repo_analysis

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
        logger.warning(f"[cycle] Review auto-complete skipped: {e}")
        return {"completed": 0}


def _phase_awareness():
    """Phase 2: Check for conflicts between active agents + attempt A2A resolution.

    Population is the union of Maiko-prepared worktrees (from
    list_prepared()) and active external sessions registered via
    /api/sessions/register. External sessions use session_id as the
    task_id in the worktree dict — that shows up as the agent label in
    conflict messages, which is fine for Phase A (consumers pass short
    IDs or we generate uuid4 hex).
    """
    try:
        from planet_maiko.brain.awareness.conflicts import detect_conflicts, resolve_conflicts
        from planet_maiko.agents.coding_agent import list_prepared
        from planet_maiko.models.external_session import ExternalSession

        prepared = list_prepared()
        worktrees = [
            {"task_id": a.get("task_id", ""), "worktree_path": a.get("working_path", "")}
            for a in prepared if a.get("working_path")
        ]

        external = ExternalSession.query.filter(
            ExternalSession.status == "active",
            ExternalSession.completed_at.is_(None),
        ).all()
        for s in external:
            if not s.worktree_path:
                continue
            worktrees.append({
                "task_id": s.session_id,
                "worktree_path": s.worktree_path,
            })

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


def _phase_calendar_focus():
    """Phase 2.5: Auto-focus from calendar events."""
    try:
        from planet_maiko.brain.focus.manager import check_calendar_focus
        from planet_maiko.models.pupdate import Pupdate
        recent = Pupdate.query.filter(Pupdate.brain_processed == False).all()  # noqa: E712
        return {"changed": check_calendar_focus(recent)}
    except Exception as e:
        logger.warning(f"[cycle] Calendar focus check skipped: {e}")
        return {"changed": False}


def _phase_automations():
    """Phase 3.2: Unified Automation engine — evaluates every active
    when/then row and fires actions.

    Replaced the old role_autonomy phase (AgentGoal-based). The engine
    reads from the Automation table; seeded + user-created watches all
    run through here. Runs before pupdate processing so emitted
    proposals get indexed the same cycle.
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


def _phase_synthesis():
    """Phase 3.8: Self-healing synthesis.

    Drains the queue of synthesized=False pr_comment signals one small
    batch at a time. Transient LLM failures during a backfill (timeout,
    malformed JSON) used to leave signals orphaned — stuck forever
    because nothing else re-synthesized them. This phase retries them
    on every cycle tick until the queue is empty.

    Capped at one batch (40 signals) per tick so the cycle stays
    snappy even when there's a big backlog.
    """
    try:
        from planet_maiko.brain.learning.synthesizer import (
            synthesize_unsynthesized_signals, BATCH_SIZE,
        )
        return synthesize_unsynthesized_signals(max_signals=BATCH_SIZE)
    except Exception as e:
        logger.warning(f"[cycle] Synthesis phase error: {e}")
        return {"found": 0, "processed": 0, "synthesized": 0, "error": str(e)}


def _phase_learning():
    """Phase 4: Aggregate feedback signals into learnings, then drift-
    dedupe the categories we just touched.

    Between-cycle duplicates happen when two signals in different
    cycles each create a new Learning with similar content (e.g.
    "prefer X over Y" and "always use X instead of Y"). The attach
    step doesn't catch these because each batch only sees its own
    Learnings at the moment it ran.

    Event-triggered dedupe: we re-cluster only the categories that
    actually changed this tick, so quiet cycles cost nothing. If no
    new signals came in, this phase is a single cheap filter query.
    """
    from planet_maiko.brain.learning.clustering import (
        cluster_signals_into_learnings, cluster_learnings,
    )
    result = cluster_signals_into_learnings()
    touched = result.get("touched_categories") or []
    if touched:
        drift = cluster_learnings(categories=touched)
        result["drift"] = drift
    return result


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
                agent_id = route(t)
                routed += 1
                logger.info(f"[cycle] Routed task {t.id} ({t.type}) -> {agent_id}")
            except Exception as e:
                # Used to be debug — but if routing silently fails, the
                # task sits forever with no agent and the user has no
                # signal anything's wrong. Bump to warning.
                logger.warning(f"[cycle] route() failed for {t.id}: {e}")

        if created or routed:
            db.session.commit()
        return {"investigation_tasks_created": created, "routed": routed}
    except Exception as e:
        # Same reasoning — orchestrate failures left tasks unrouted
        # silently. Warn so it actually shows up in logs.
        logger.warning(f"[cycle] Orchestrate skipped: {e}")
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
        logger.warning(f"[cycle] Unblock phase skipped: {e}")
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
            if existing:
                # Already surfaced once — respect the user's dismissal.
                # If they've decided they know about it, don't keep
                # reminding them every cycle. (And the pupdate ID is
                # fixed at `stuck-{task_id[:32]}`, so a second insert
                # would PK-conflict anyway.) The auto-dismiss pass
                # below still closes the pupdate when the task moves
                # out of in_progress.
                continue

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
        logger.warning(f"[cycle] Stuck escalation skipped: {e}")
        return {"escalated": 0, "auto_dismissed": 0}


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


def _phase_spawn_jobs_for_tasks():
    """Phase 8b: Turn assigned review/pr_review Tasks into AgentJobs.

    User-owed review Tasks stay in the Tasks page (they represent the
    *request* — "please review this PR"). The execution of the review
    is a pack-owned AgentJob that carries the worktree, session, and
    artifact. This phase is the bridge.

    Picks up any review/pr_review Task with an assigned agent and no
    linked AgentJob yet, and queues a job. The execute_agent_jobs
    phase picks it up next tick.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.database import db
    from planet_maiko.orchestration import scope_for_task
    import uuid as _uuid

    try:
        candidates = Task.query.filter(
            Task.type.in_(["review", "pr_review"]),
            Task.status.in_(["new", "blocked"]),
            Task.assigned_agent_id.isnot(None),
        ).limit(10).all()
        if not candidates:
            return {"spawned": 0}

        spawned = 0
        for task in candidates:
            # Skip if a job already exists for this task.
            existing = AgentJob.query.filter_by(source_task_id=task.id).first()
            if existing:
                continue

            extra = task.extra or {}
            job = AgentJob(
                id=_uuid.uuid4().hex[:24],
                kind=task.type,
                title=task.title,
                description=extra.get("description") or extra.get("body"),
                scope_repo=scope_for_task(task),
                priority=task.priority or "normal",
                created_by="system",
                source_task_id=task.id,
                agent_profile_id=task.assigned_agent_id,
                status="queued",
                extra={},
            )
            db.session.add(job)
            spawned += 1
            logger.info(f"[cycle] spawned AgentJob for {task.type} task {task.id}")

        if spawned:
            db.session.commit()
        return {"spawned": spawned}
    except Exception as e:
        logger.warning(f"[cycle] Spawn jobs for tasks skipped: {e}")
        return {"spawned": 0, "error": str(e)}


def _phase_execute_agent_jobs():
    """Phase 8c: run queued AgentJobs. Sibling of _phase_execute_agent_tasks
    — same prepare+headless-kickoff machinery, reading from the AgentJob
    table instead of Task.

    Skips pending_approval (user hasn't approved yet) and running / done /
    failed / cancelled. Capped at 2 per cycle to bound token spend.

    When the job has a source_task_id (Stage D review/pr_review), we
    compose the full TASK.md via build_task_prompt(task, role) so the
    agent sees everything the Task-direct path saw: source pupdate,
    project, url, tags, skill prompt. The linked Task's status moves
    to in_progress on kickoff.
    """
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.task import Task
    from planet_maiko.database import db
    from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE
    from planet_maiko.agents.coding_agent import prepare, _kickoff_agent_headless
    from planet_maiko.orchestration import resolve_repo_path, maybe_spawn, build_task_prompt

    try:
        candidates = (
            AgentJob.query
            .filter(AgentJob.status == "queued")
            .order_by(AgentJob.created_at.asc())
            .limit(2)
            .all()
        )
        if not candidates:
            return {"executed": 0}

        executed = 0
        for job in candidates:
            role = (ONE_SHOT_ROLE_FOR_TYPE.get(job.kind) or (None, None))[0]
            if role is None:
                role = {
                    "cartograph": "cartographer",
                    "review": "review",
                    "pr_review": "review",
                }.get(job.kind, "investigation")

            local_path = resolve_repo_path(job.scope_repo) if job.scope_repo else None
            if job.scope_repo and not local_path:
                logger.warning(
                    f"[cycle] agent_job {job.id}: no local clone for "
                    f"{job.scope_repo}, marking failed"
                )
                job.status = "failed"
                job.error = f"No local clone found for {job.scope_repo}"
                job.finished_at = datetime.now(timezone.utc)
                db.session.commit()
                continue

            # Find or spawn an agent profile for this role+scope.
            if job.agent_profile_id:
                profile = db.session.get(AgentProfile, job.agent_profile_id)
            else:
                profile = maybe_spawn(role, job.scope_repo)
                job.agent_profile_id = profile.id

            # If this job is linked to a Task, use the shared prompt
            # composer so the agent sees the full Task context
            # (source pupdate, project, url, tags, skill recipe).
            linked_task = (
                db.session.get(Task, job.source_task_id)
                if job.source_task_id else None
            )
            if linked_task is not None:
                full_prompt = build_task_prompt(linked_task, role)
            else:
                # Lightweight prompt for standalone AgentJobs.
                prompt_parts = [job.title]
                if job.description:
                    prompt_parts.append(f"\n## Description\n\n{job.description}")
                if role in ("review", "investigation"):
                    try:
                        from planet_maiko.agents.skills import get_skill_prompt
                        skill_name = (ONE_SHOT_ROLE_FOR_TYPE.get(job.kind) or (None, None))[1]
                        if skill_name:
                            skill_prompt = get_skill_prompt(skill_name, {
                                "query": job.title,
                                "context": f"Repo: {job.scope_repo or ''}",
                                "pupdates": "[]", "tasks": "[]", "calendar": "[]",
                            }) or ""
                            if skill_prompt.strip():
                                prompt_parts.append(f"\n## Skill: {skill_name}\n\n{skill_prompt}")
                    except Exception as e:
                        logger.debug(f"[cycle] skill prompt embed skipped: {e}")
                full_prompt = "\n".join(prompt_parts)

            if not job.worktree_path:
                if not local_path:
                    # Cartograph + some skill runs can work without a
                    # repo clone (read-only on current working dir), but
                    # agent runs without a real repo are unusual. Mark
                    # failed if we can't resolve a path.
                    logger.warning(f"[cycle] agent_job {job.id}: no repo path, skipping")
                    continue
                try:
                    prep = prepare(
                        task_id=job.id,
                        task_title=job.title,
                        prompt=full_prompt,
                        repo_path=local_path,
                        branch_prefix="cartographer" if role == "cartographer" else "maiko",
                        auto_kickoff=False,
                        use_worktree=True,
                        agent_profile_id=job.agent_profile_id,
                        role=role,
                    )
                except Exception as e:
                    logger.warning(f"[cycle] prepare failed for agent_job {job.id}: {e}")
                    job.status = "failed"
                    job.error = str(e)[:500]
                    job.finished_at = datetime.now(timezone.utc)
                    db.session.commit()
                    continue
                if not prep:
                    continue
                job.worktree_path = prep["working_path"]
                job.branch = prep.get("branch")
                db.session.commit()

            kickoff = _kickoff_agent_headless(
                job.agent_profile_id, job.worktree_path, job.id,
                branch_name=None, plan_first=False, role=role,
            )
            if kickoff.get("success"):
                job.status = "running"
                job.started_at = datetime.now(timezone.utc)
                job.session_id = kickoff.get("session_id")
                # Sync the linked Task: status → in_progress, and
                # surface the worktree path on task.extra so UI code
                # (diff view, relaunch) that still reads from Task
                # doesn't break.
                if linked_task is not None:
                    linked_task.status = "in_progress"
                    task_extra = dict(linked_task.extra or {})
                    if job.worktree_path:
                        task_extra["working_path"] = job.worktree_path
                    if job.branch:
                        task_extra["branch"] = job.branch
                    linked_task.extra = task_extra
                executed += 1
            else:
                job.status = "failed"
                job.error = kickoff.get("error") or "kickoff failed"
                job.finished_at = datetime.now(timezone.utc)
                logger.warning(
                    f"[cycle] kickoff failed for agent_job {job.id}: {kickoff.get('error')}"
                )

        if executed:
            db.session.commit()
        return {"executed": executed}
    except Exception as e:
        logger.warning(f"[cycle] execute agent jobs skipped: {e}")
        return {"executed": 0, "error": str(e)}


def _phase_execute_agent_tasks():
    """Phase 8d: Safety net for non-review one-shot tasks that have been
    assigned but haven't started yet.

    Post-Stage D this only handles investigation / repo_analysis /
    cartograph. Review / pr_review Tasks now route through
    _phase_spawn_jobs_for_tasks → _phase_execute_agent_jobs so the
    AgentJob owns the worktree + artifact.

    Every non-review role shares the same autonomous flow —
    claude --print in a worktree, agent uses the channel MCP to reply
    ready_for_review. /agents/assign fires this inline; this phase is
    the catch-all for tasks routed by _phase_orchestrate (which only
    writes assigned_agent_id, no kickoff) or whose inline kickoff
    thread died silently.

    Coding tasks are excluded because their kickoff is owned by
    coding_agent.kickoff_coding_task (called from the project
    plan-approve path) and re-firing them here would step on user
    intent. One-shot tasks are idempotent enough — the agent
    re-reads TASK.md and produces the same artifact.

    Capped at 2 per cycle to bound token spend.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.database import db
    from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE
    from planet_maiko.agents.coding_agent import prepare, _kickoff_agent_headless
    from planet_maiko.orchestration import resolve_repo_path

    _NON_REVIEW_ONE_SHOT = [
        k for k in ONE_SHOT_ROLE_FOR_TYPE.keys()
        if k not in ("review", "pr_review")
    ]

    try:
        candidates = Task.query.filter(
            Task.status == "new",
            Task.assigned_agent_id.isnot(None),
            Task.type.in_(_NON_REVIEW_ONE_SHOT),
        ).limit(2).all()

        if not candidates:
            return {"executed": 0}

        executed = 0
        for task in candidates:
            meta = task.extra or {}
            working_dir = meta.get("working_path")
            profile = db.session.get(AgentProfile, task.assigned_agent_id)
            role = (profile.role if profile else None) or {
                "review": "review", "pr_review": "review",
                "investigation": "investigation",
                "repo_analysis": "investigation",
            }.get(task.type, "investigation")

            # First time through for this task — set up the worktree.
            if not working_dir:
                from planet_maiko.orchestration import scope_for_task
                repo = scope_for_task(task)
                local_path = resolve_repo_path(repo)
                if not local_path:
                    _emit_missing_clone_pupdate(task, repo)
                    logger.warning(
                        f"[cycle] task {task.id}: no local clone for "
                        f"{repo}, skipping (added pupdate)"
                    )
                    continue
                try:
                    from planet_maiko.orchestration import build_task_prompt
                    # Same composer the assign API and pack dispatcher use,
                    # so a task entering via the cycle's safety net gets the
                    # full description + source context + project + skill
                    # prompt the other entry points include. The earlier
                    # inline version was missing several of these, so a
                    # task retried via the cycle arrived with less context
                    # than the same task via the API.
                    full_prompt = build_task_prompt(task, role)
                    prep = prepare(
                        task_id=task.id,
                        task_title=task.title,
                        prompt=full_prompt,
                        repo_path=local_path,
                        auto_kickoff=False,
                        use_worktree=True,
                        agent_profile_id=task.assigned_agent_id,
                        role=role,
                    )
                except Exception as e:
                    logger.warning(f"[cycle] prepare failed for {task.id}: {e}")
                    continue
                if not prep:
                    continue
                working_dir = prep["working_path"]
                task.extra = {**meta, "working_path": working_dir, "branch": prep["branch"]}
                db.session.commit()

            # Same headless flow coding agents use — claude --print,
            # daemon thread, channel MCP for replies. Returns
            # immediately after spawning.
            kickoff = _kickoff_agent_headless(
                task.assigned_agent_id, working_dir, task.id,
                branch_name=None, plan_first=False, role=role,
            )
            if kickoff.get("success"):
                task.status = "in_progress"
                executed += 1
            else:
                logger.warning(
                    f"[cycle] kickoff failed for {task.id}: {kickoff.get('error')}"
                )

        if executed:
            db.session.commit()
        return {"executed": executed}
    except Exception as e:
        logger.warning(f"[cycle] Execute agent tasks skipped: {e}")
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
    ("automations", _phase_automations),
    ("pupdates", _phase_pupdates),
    ("synthesis", _phase_synthesis),
    ("learning", _phase_learning),
    ("heartbeats", _phase_heartbeats),
    ("projects", _phase_projects),
    ("orchestrate", _phase_orchestrate),
    ("unblock", _phase_unblock_tasks),
    ("spawn_jobs_for_tasks", _phase_spawn_jobs_for_tasks),
    ("execute_agent_jobs", _phase_execute_agent_jobs),
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
