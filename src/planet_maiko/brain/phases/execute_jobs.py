"""Brain-cycle phase: execute pending AgentJobs.

Pairs with _phase_spawn_jobs_for_tasks. Job rows seen here were
created by the previous phase or by an Automation; this phase
spawns the runner for each.
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




def _phase_execute_agent_jobs():
    """Phase 8c: run queued AgentJobs. Sibling of _phase_execute_agent_tasks
    â€” same prepare+headless-kickoff machinery, reading from the AgentJob
    table instead of Task.

    Skips pending_approval (user hasn't approved yet) and running / done /
    failed / cancelled. Capped at 2 per cycle to bound token spend.

    Three execution shapes:
      - Specialty with needs_worktree=False: direct LLM call via
        runtime.send. Output lands as a skill_result memo, no worktree,
        no MCP round-trip. Runs synchronously inside the cycle.
      - Specialty with needs_worktree=True: the existing prepare +
        _kickoff_agent_headless path, with the specialty's protocol
        embedded into the prompt.
      - Built-in roles (review / investigation / cartograph): unchanged
        â€” same path, same prompt composition as before.
    """
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.task import Task
    from planet_maiko.models.custom_skill import CustomSkill
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
            # Specialty fast path: no-worktree specialties run as a
            # direct LLM call, not the full prepare+kickoff dance.
            specialty = db.session.get(CustomSkill, job.kind)
            if specialty is not None and not specialty.needs_worktree:
                if _execute_lightweight_specialty(job, specialty):
                    executed += 1
                continue

            role = (ONE_SHOT_ROLE_FOR_TYPE.get(job.kind) or (None, None))[0]
            if role is None and specialty is not None:
                # needs_worktree=True specialty: role IS the specialty
                # id so maybe_spawn creates an agent with the specialty
                # role, and the skill-prompt embed below picks up the
                # specialty's protocol.
                role = specialty.id
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
                _bump_agent_failed(job.agent_profile_id)
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

                # Fold in the triggering pupdate(s) so a chain-fired agent
                # can see the incident / CI failure / error-spike bodies
                # that made this automation fire. Without this, the agent
                # only sees a templated title + static description and has
                # to go fish for context.
                try:
                    from planet_maiko.models.pupdate import Pupdate as _Pup
                    job_extra = job.extra or {}
                    pupdate_ids = []
                    single_id = job_extra.get("triggered_by_pupdate")
                    if single_id:
                        pupdate_ids.append(single_id)
                    for pid in (job_extra.get("triggered_by_pupdates") or []):
                        if pid not in pupdate_ids:
                            pupdate_ids.append(pid)
                    if pupdate_ids:
                        triggering = (
                            _Pup.query
                            .filter(_Pup.id.in_(pupdate_ids))
                            .order_by(_Pup.timestamp.asc())
                            .all()
                        )
                        if triggering:
                            heading = (
                                "Triggering event" if len(triggering) == 1
                                else f"Triggering events ({len(triggering)})"
                            )
                            lines = [f"\n## {heading}\n"]
                            for p in triggering:
                                lines.append(f"### {p.type}: {p.title or '(no title)'}")
                                if p.source:
                                    lines.append(f"Source: {p.source}")
                                if p.url:
                                    lines.append(f"URL: {p.url}")
                                if p.tags:
                                    lines.append(f"Tags: {', '.join(p.tags)}")
                                if p.body:
                                    lines.append(f"\n{p.body}")
                                lines.append("")
                            prompt_parts.append("\n".join(lines))
                except Exception as e:
                    logger.debug(f"[cycle] pupdate-context enrich skipped for {job.id}: {e}")

                # Embed the skill/specialty protocol into the prompt
                # for roles whose work is guided by one. Specialties
                # use job.kind directly (it's the CustomSkill.id);
                # built-in review/investigation roles pick up from
                # ONE_SHOT_ROLE_FOR_TYPE's skill mapping.
                skill_name = None
                if specialty is not None:
                    skill_name = specialty.id
                elif role in ("review", "investigation"):
                    skill_name = (ONE_SHOT_ROLE_FOR_TYPE.get(job.kind) or (None, None))[1]
                if skill_name:
                    try:
                        from planet_maiko.agents.skills import get_skill_prompt
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
                # Specialty picked at automation-config time or via ask-
                # first approval lives on job.extra. prepare() safety-
                # checks it against the agent's attached pool; runner
                # silently drops unattached ids.
                specialty_id = (job.extra or {}).get("specialty_id") or None
                try:
                    prep = prepare(
                        task_id=job.id,
                        task_title=job.title,
                        prompt=full_prompt,
                        repo_path=local_path,
                        branch_prefix="cartographer" if role == "cartographer" else "maiko",
                        use_worktree=True,
                        agent_profile_id=job.agent_profile_id,
                        role=role,
                        specialty_id=specialty_id,
                    )
                except Exception as e:
                    logger.warning(f"[cycle] prepare failed for agent_job {job.id}: {e}")
                    job.status = "failed"
                    job.error = str(e)[:500]
                    job.finished_at = datetime.now(timezone.utc)
                    _bump_agent_failed(job.agent_profile_id)
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
                # Sync the linked Task: status â†’ in_progress, and
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
                _bump_agent_failed(job.agent_profile_id)
                logger.warning(
                    f"[cycle] kickoff failed for agent_job {job.id}: {kickoff.get('error')}"
                )

        if executed:
            db.session.commit()
        return {"executed": executed}
    except Exception as e:
        logger.warning(f"[cycle] execute agent jobs skipped: {e}")
        return {"executed": 0, "error": str(e)}