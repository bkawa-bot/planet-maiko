"""Brain-cycle phase: execute lightweight Task-typed runs.

Legacy execute path — most one-shot work migrated to AgentJob, but
a handful of Task types still spawn directly off Tasks. Kept on its
own so cycle.py stays an orchestrator.
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




def _phase_execute_agent_tasks():
    """Phase 8d: Safety net for non-review one-shot tasks that have been
    assigned but haven't started yet.

    Post-Stage D this only handles investigation / repo_analysis /
    cartograph. Review / pr_review Tasks now route through
    _phase_spawn_jobs_for_tasks â†’ _phase_execute_agent_jobs so the
    AgentJob owns the worktree + artifact.

    Every non-review role shares the same autonomous flow â€”
    claude --print in a worktree, agent uses the channel MCP to reply
    ready_for_review. /agents/assign fires this inline; this phase is
    the catch-all for tasks routed by _phase_orchestrate (which only
    writes assigned_agent_id, no kickoff) or whose inline kickoff
    thread died silently.

    Coding tasks are excluded because their kickoff is owned by
    coding_agent.kickoff_coding_task (called from the project
    plan-approve path) and re-firing them here would step on user
    intent. One-shot tasks are idempotent enough â€” the agent
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

            # First time through for this task â€” set up the worktree.
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
                    # Specialty picked at assign time lives on
                    # task.extra â€” pass through so the retry-via-cycle
                    # path builds the same CLAUDE.md the fresh assign
                    # would have.
                    specialty_id = (task.extra or {}).get("specialty_id") or None
                    prep = prepare(
                        task_id=task.id,
                        task_title=task.title,
                        prompt=full_prompt,
                        repo_path=local_path,
                        use_worktree=True,
                        agent_profile_id=task.assigned_agent_id,
                        role=role,
                        specialty_id=specialty_id,
                    )
                except Exception as e:
                    logger.warning(f"[cycle] prepare failed for {task.id}: {e}")
                    continue
                if not prep:
                    continue
                working_dir = prep["working_path"]
                task.extra = {**meta, "working_path": working_dir, "branch": prep["branch"]}
                db.session.commit()

            # Same headless flow coding agents use â€” claude --print,
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