"""Brain-cycle phase: execute pending AgentJobs.

Pairs with _phase_spawn_jobs_for_tasks. Job rows seen here were
created by the previous phase or by an Automation; this phase
spawns the runner for each.
"""

import logging
import re
from datetime import datetime, timezone

from ._helpers import _bump_agent_failed

logger = logging.getLogger(__name__)

# https://github.com/<org>/<repo>/pull/<number> — capture the trailing
# number. Tolerant of anchors / query strings since pollers sometimes
# append #pullrequestreview or ?... on the URL.
_PR_NUMBER_RE = re.compile(r"github\.com/[^/]+/[^/]+/pull/(\d+)")


def _extract_pr_number(url):
    """Pull the PR number out of a GitHub PR url. None when the url
    is empty or doesn't match the pull-request shape (issues, blob
    URLs, non-github links, etc)."""
    if not url:
        return None
    m = _PR_NUMBER_RE.search(url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


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
    from planet_maiko.agents.runtime import prepare, _kickoff_agent_headless
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

            # Prefer an explicit repo_path the user picked at assign
            # time (coding tasks let them point at any local clone, not
            # just the configured ones). Fall back to resolving from
            # scope_repo + repo_roots when no explicit path is set.
            local_path = (job.extra or {}).get("repo_path") or (
                resolve_repo_path(job.scope_repo) if job.scope_repo else None
            )
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
                # custom_prompt comes from the assign endpoint when the
                # user typed a one-off intent override into the modal.
                # Persisted on job.extra so a queued job retains it
                # across the cycle delay.
                custom_prompt = (job.extra or {}).get("custom_prompt", "")
                full_prompt = build_task_prompt(linked_task, role, custom_prompt)
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
                    # No clone resolved — surface this as an explicit
                    # failure rather than silently skipping every cycle.
                    # The two upstream paths to here are (a) job.scope_repo
                    # set but no local clone — the earlier check at the
                    # top of this loop handles that — and (b) job.scope_repo
                    # never set at all, which can happen when the
                    # triggering pupdate's metadata didn't include a repo.
                    # Either way, the job can't run; mark it so the user
                    # sees the reason instead of a job that sits queued
                    # forever.
                    logger.warning(
                        f"[cycle] agent_job {job.id}: no scope_repo set "
                        f"and no local path resolvable — marking failed"
                    )
                    job.status = "failed"
                    job.error = (
                        "No scope_repo on the job (the triggering "
                        "pupdate didn't carry a repo, or the repo isn't "
                        "in config.github.repos). Set the repo on the "
                        "job or the source pupdate and re-queue."
                    )
                    job.finished_at = datetime.now(timezone.utc)
                    _bump_agent_failed(job.agent_profile_id)
                    db.session.commit()
                    continue
                # Specialty picked at automation-config time or via ask-
                # first approval lives on job.extra. prepare() safety-
                # checks it against the agent's attached pool; runner
                # silently drops unattached ids.
                job_extra_for_prep = job.extra or {}
                specialty_id = job_extra_for_prep.get("specialty_id") or None
                # Branch prefix preference: explicit user choice from the
                # assign endpoint > role default. cartographer keeps its
                # own prefix so its scratch branches read as "cartographer
                # mapping <repo>" instead of generic "maiko/...".
                branch_prefix = (
                    job_extra_for_prep.get("branch_prefix")
                    or ("cartographer" if role == "cartographer" else "maiko")
                )
                # For review jobs, derive the PR number from the linked
                # task's URL so the worktree is built from the PR's head
                # instead of main. Without this the agent reviewed an
                # empty diff. Falls back to None on non-review roles or
                # when the URL doesn't match a github PR pattern; the
                # worktree then behaves as before.
                pr_number = None
                if role == "review" and linked_task is not None:
                    pr_number = _extract_pr_number(linked_task.url)
                    if pr_number is None:
                        pr_number = _extract_pr_number(
                            (linked_task.extra or {}).get("pr_url")
                        )
                try:
                    prep = prepare(
                        task_id=job.id,
                        task_title=job.title,
                        prompt=full_prompt,
                        repo_path=local_path,
                        branch_prefix=branch_prefix,
                        agent_profile_id=job.agent_profile_id,
                        role=role,
                        specialty_id=specialty_id,
                        pr_number=pr_number,
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

            # Carry plan_first / branch_name from job.extra so coding
            # tasks queued via the assign endpoint keep their plan-first
            # flag and any user-picked branch prefix. branch_name on
            # _kickoff_agent_headless is unused at runtime (worktree
            # mode), but plan_first changes the initial prompt.
            job_extra = job.extra or {}
            kickoff_plan_first = bool(job_extra.get("plan_first")) and role == "coding"
            kickoff = _kickoff_agent_headless(
                job.agent_profile_id, job.worktree_path, job.id,
                branch_name=None, plan_first=kickoff_plan_first, role=role,
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
            # Commit per-iteration so a failed kickoff persists. The
            # earlier end-of-loop commit only fired when executed > 0,
            # so a job whose kickoff returned {success: False} (e.g.
            # claude CLI missing on PATH, branch-name guard, lock held)
            # would silently revert from failed → queued on the next
            # cycle and loop forever. Per-iteration commit also
            # isolates one job's failure from the next.
            db.session.commit()

        return {"executed": executed}
    except Exception as e:
        logger.warning(f"[cycle] execute agent jobs skipped: {e}")
        return {"executed": 0, "error": str(e)}