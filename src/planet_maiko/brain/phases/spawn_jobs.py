"""Brain-cycle phase: spawn AgentJobs for tasks that need them.

Lifted out of cycle.py — biggest single phase by line count, has
its own preparation / specialty resolution that doesn't belong in
the orchestrator file. cycle.py imports the phase function back so
the _PHASES list still references it.
"""

"""Brain cycle - the clock tick that drives all processors.

Each cycle runs all phases in order, just like a CPU executes its
pipeline on each clock tick. Each phase is its own function so failures
are isolated and the orchestrator stays readable.

Pipeline (phases run in this order):
    1.  agents               â€” process agent pupdates (auto-complete tasks)
    1.5 auto_complete_reviews â€” close review tasks for approved/merged PRs
    2.  awareness            â€” A2A conflict detection + resolution
    2.5 calendar_focus       â€” auto-focus from calendar events
    3.2 automations          â€” evaluate user-editable when/then rows (replaced correlator)
    3.5 pupdates             â€” match remaining pupdates against rules
    3.6 llm_triage           â€” Tier 2 LLM triage for unmatched pupdates
    4.  learning             â€” aggregate signals into learnings
    5.  heartbeats           â€” nudge silent agents
    6.  projects             â€” auto-advance project phases
    7.  scheduled_skills     â€” run skills on their schedules
    8.  orchestrate          â€” materialize investigation tasks + route
                               unassigned tasks to agent profiles
    8b. unblock              â€” cascade depends_on completion
    8b2.spawn_jobs_for_tasks â€” turn assigned review tasks into AgentJobs
    8c. execute_agent_jobs   â€” run queued AgentJobs (review + pack-owned)
    8d. execute_agent_tasks  â€” safety net for investigation/repo_analysis

Note: morning brief is user-triggered from the Home page (not a cycle
phase â€” nobody wants a "morning" brief running at 3am when the first
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




def _phase_spawn_jobs_for_tasks():
    """Phase 8b: Turn assigned review/pr_review Tasks into AgentJobs.

    User-owed review Tasks stay in the Tasks page (they represent the
    *request* â€” "please review this PR"). The execution of the review
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

        # Diagnostic: surface review tasks the spawn phase is *missing*
        # because they have no assigned agent. Without this log line
        # a pupdateâ†’taskâ†’(no job) gap is invisible from the outside.
        # Capped at the same limit + cheap query; only logs when there
        # actually are stranded review tasks.
        stranded = Task.query.filter(
            Task.type.in_(["review", "pr_review"]),
            Task.status.in_(["new", "blocked"]),
            Task.assigned_agent_id.is_(None),
        ).limit(10).all()
        for t in stranded:
            logger.warning(
                f"[cycle] review task {t.id} ({t.type}) has no assigned "
                f"agent â€” spawn skipped. status={t.status}, "
                f"repo={(t.extra or {}).get('repo')!r}"
            )

        if not candidates:
            return {"spawned": 0}

        spawned = 0
        for task in candidates:
            # Skip if an ACTIVE job already exists for this task. A
            # previous review round that's done/cancelled/failed
            # shouldn't block a fresh spawn â€” e.g. the PR author
            # re-requested review after pushing new commits, the task
            # went "waiting" â†’ "new", and we want another review pass
            # against the updated diff.
            existing = (
                AgentJob.query
                .filter_by(source_task_id=task.id)
                .filter(AgentJob.status.in_([
                    "pending_approval", "queued", "running",
                ]))
                .first()
            )
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


def _bump_agent_failed(agent_profile_id):
    """Increment the agent profile's tasks_failed counter. No-op when
    the profile id is unknown (stray job with no assigned agent).
    Caller is responsible for committing the session."""
    if not agent_profile_id:
        return
    from planet_maiko.models.agent_profile import AgentProfile as _AP
    from planet_maiko.database import db as _db
    prof = _db.session.get(_AP, agent_profile_id)
    if prof is not None:
        prof.tasks_failed = (prof.tasks_failed or 0) + 1


def _execute_lightweight_specialty(job, specialty):
    """Run a no-worktree specialty as a single synchronous LLM call.

    The full prepare + _kickoff_agent_headless machinery exists for
    long-running agents that need a worktree + MCP reply channel.
    Specialties like brainstorm / plan / verify just compose a prompt
    from DB state and call the LLM once â€” that overhead is pure waste
    for them. This path:

      1. Lazy-spawn an agent for role=specialty.id / scope=job.scope_repo
         (if not already assigned).
      2. Compose the specialty's prompt via get_skill_prompt, which
         handles context injection + voice + user-edit overrides.
      3. Call runtime.send synchronously (bounded by timeout).
      4. Parse PATTERN / PROPOSAL / TASK blocks if the job is linked
         to a Task (same mechanism investigation agents use).
      5. Write a skill_result Memo so the output lands in Recent Skills.
      6. Mark job done; if linked to a Task, mark that done too.

    Returns True on success, False on failure. Failures update the job
    with status=failed + error so the caller's committed.
    """
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.task import Task
    from planet_maiko.database import db
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model
    from planet_maiko.agents.skills import get_skill_prompt
    from planet_maiko.brain.memos import create_memo
    from planet_maiko.brain.learning.agent_output import parse_and_apply_blocks
    from planet_maiko.orchestration import maybe_spawn

    # Ensure we have an agent for this specialty + scope.
    if job.agent_profile_id:
        agent = db.session.get(AgentProfile, job.agent_profile_id)
    else:
        agent = maybe_spawn(specialty.id, job.scope_repo)
        job.agent_profile_id = agent.id

    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        job.status = "failed"
        job.error = "LLM runtime unavailable"
        job.finished_at = datetime.now(timezone.utc)
        logger.warning(f"[cycle] specialty {specialty.id} ({job.id}): runtime unavailable")
        return False

    from planet_maiko.brain.automations import format_pupdate_for_context
    pupdate_block = format_pupdate_for_context(
        (job.extra or {}).get("pupdate_snapshot")
    )
    context_parts = [pupdate_block, (job.description or "")]
    if job.scope_repo:
        context_parts.append(f"Repo: {job.scope_repo}")
    context = {
        "query": job.title,
        "context": "\n\n".join(p for p in context_parts if p),
        "pupdates": "[]",
        "tasks": "[]",
        "calendar": "[]",
    }
    prompt = get_skill_prompt(specialty.id, context) or specialty.prompt

    model = resolve_model(f"skill:{specialty.id}") or resolve_model("skill")
    try:
        result = runtime.send(prompt, timeout=300, model=model)
    except Exception as e:
        job.status = "failed"
        job.error = str(e)[:500]
        job.finished_at = datetime.now(timezone.utc)
        logger.warning(f"[cycle] specialty {specialty.id} ({job.id}): send failed: {e}")
        return False

    if not result or not result.get("success"):
        err = (result or {}).get("error") or "LLM call failed"
        job.status = "failed"
        job.error = str(err)[:500]
        job.finished_at = datetime.now(timezone.utc)
        logger.warning(f"[cycle] specialty {specialty.id} ({job.id}): {err}")
        return False

    output = (result.get("output") or "").strip()

    # Parse structured blocks. Needs a task for attribution â€” without
    # one, specialty runs don't feed signals / proposals into the
    # pool. Standalone specialty jobs still produce a readable memo.
    linked_task = db.session.get(Task, job.source_task_id) if job.source_task_id else None
    cleaned = output
    if agent is not None and linked_task is not None:
        try:
            parsed = parse_and_apply_blocks(
                output, agent=agent, task=linked_task, repo=job.scope_repo,
            )
            cleaned = parsed.get("cleaned_output", output)
        except Exception as e:
            logger.debug(f"[cycle] block parse failed for {specialty.id}: {e}")

    # Write the user-facing memo.
    first_line = ""
    for line in cleaned.splitlines():
        stripped = line.strip().lstrip("# ").strip()
        if stripped:
            first_line = stripped
            break
    preview = first_line[:80] if first_line else specialty.name
    create_memo(
        kind="skill_result",
        category="info",
        title=f"{specialty.id}: {preview}",
        body=cleaned[:16000],
        priority=job.priority or "normal",
        source_agent_id=job.agent_profile_id,
        source_task_id=job.source_task_id,
        extra={
            "skill_name": specialty.id,
            "skill_title": specialty.name,
            "from_agent_job": job.id,
        },
    )

    job.status = "done"
    job.finished_at = datetime.now(timezone.utc)
    job.artifact = cleaned[:16000] or None
    if linked_task is not None:
        linked_task.status = "done"
        task_extra = dict(linked_task.extra or {})
        if cleaned:
            task_extra["artifact"] = cleaned[:16000]
        linked_task.extra = task_extra
    db.session.commit()

    logger.info(
        f"[cycle] specialty {specialty.id} ({job.id}) done by {agent.display_name if agent else 'unknown'}: "
        f"{len(cleaned)} chars"
    )
    return True