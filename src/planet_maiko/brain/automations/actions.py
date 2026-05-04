"""Action executors for the Automation engine.

Each `_act_*` function performs a side-effect and returns either
None (just did the work), or a dict to feed the next action's
context. The ACTIONS dict at the bottom is the dispatch table.

Helpers _interpolate / _pupdate_snapshot / format_pupdate_for_context
live here because actions are the only module that templates strings
or snapshots pupdate state — conditions just match and return.
"""

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.automation import Automation
from planet_maiko.models.pupdate import Pupdate

from .helpers import _safe_format

logger = logging.getLogger(__name__)




# ---------------------------------------------------------------------------
# Action dispatchers — each returns a short result dict for logging.
# ---------------------------------------------------------------------------

def _act_run_agent_job(automation, config, pupdate=None, context=None):
    """Spawn an AgentJob — the pack-owned "run this skill / role" primitive.

    Config:
      kind: str           — job kind (cartograph, investigation,
                            repo_analysis, or a skill name). Maps to
                            task.type in the execute phase — determines
                            which role spawns.
      title: str          — defaults to automation.name.
      description: str    — skill input / body / instructions for the agent.
      scope_repo: str     — org/repo; drives worktree resolution. When
                            unset, falls back to the chain condition's
                            `service` (shared repo across the matched
                            pupdates) or the automation's own scope.
      priority: str       — low | normal | high | urgent. Default normal.
      ask_first: bool     — true → status=pending_approval (user approves
                            from the AgentJobs dashboard). false → direct
                            queue for the next execute-phase tick.
    """
    from planet_maiko.models.agent_job import AgentJob

    ctx = context or {}
    ask_first = bool(config.get("ask_first", False))
    kind = config.get("kind") or "todo"
    title = config.get("title") or automation.name
    # Optional specialty — extra context the agent picks up on top of
    # the role protocol. Persisted on the job so the execute phase can
    # hand it to prepare() when the worktree spins up.
    specialty_id = (config.get("specialty_id") or "").strip() or None
    # Pick the right repo for the spawned agent's worktree. Priority:
    #   1. Explicit config.scope_repo (user overrode it)
    #   2. automation.scope_repo (hand-authored automations still work)
    #   3. Chain condition's `service` (shared repo across a pupdate chain)
    #   4. Wildcard condition's `repo` (overview_stale / lora_missing
    #      picked a specific repo this cycle)
    #   5. Pupdate's own repo when pupdate-scope fired
    # Without (4) and (5), a single "all repos" automation couldn't
    # route the job to the repo that actually triggered it.
    pupdate_repo = (pupdate.extra or {}).get("repo") if pupdate is not None else None
    repo = (
        config.get("scope_repo")
        or automation.scope_repo
        or ctx.get("service")
        or ctx.get("repo")
        or pupdate_repo
        or None
    )
    description = config.get("description") or automation.description or ""
    priority = config.get("priority") or "normal"

    extra = {"from_automation": automation.id}
    if pupdate is not None:
        extra["triggered_by_pupdate"] = pupdate.id
        snap = _pupdate_snapshot(pupdate)
        if snap:
            extra["pupdate_snapshot"] = snap
    chain_ids = ctx.get("pupdate_ids") or []
    if chain_ids:
        extra["triggered_by_pupdates"] = list(chain_ids)
    if specialty_id:
        extra["specialty_id"] = specialty_id

    # Ask-first path: no AgentJob is created yet. A kind=job_approval
    # Memo carries the full job spec in extra.job_spec, and
    # /memos/<id>/approve (see brain/memo_handlers.py) mints the real
    # AgentJob with status=queued when the user approves. Stops phantom
    # "might never run" AgentJobs from sitting in the DB.
    if ask_first:
        from planet_maiko.brain.memos import create_memo
        memo_extra = {
            **extra,
            "job_spec": {
                "kind": kind,
                "title": title,
                "description": description,
                "scope_repo": repo,
                "priority": priority,
                "automation_id": automation.id,
            },
        }
        memo = create_memo(
            kind="job_approval",
            category="offer",
            title=title,
            body=description or None,
            priority=priority,
            cta_label="Approve",
            cta_action="approve",
            source_pupdate_id=pupdate.id if pupdate is not None else None,
            extra=memo_extra,
        )
        db.session.flush()
        return {
            "memo_id": memo.id,
            "kind": "run_agent_job",
            "status": "awaiting_approval",
        }

    # No approval gate: mint the AgentJob directly.
    job_id = f"job-{uuid.uuid4().hex[:10]}"
    job = AgentJob(
        id=job_id,
        kind=kind,
        title=title,
        description=description,
        scope_repo=repo,
        priority=priority,
        created_by="automation",
        automation_id=automation.id,
        requires_approval=False,
        status="queued",
        approved_by="auto",
        approved_at=datetime.now(timezone.utc),
        extra=extra,
    )
    db.session.add(job)
    # Flush early so DB errors surface here rather than getting
    # silently rolled back at the cycle's final commit. Same
    # rationale as _act_spawn_agent_job_from_pupdate.
    try:
        db.session.flush()
    except Exception as e:
        logger.warning(
            f"[automation {automation.id}] AgentJob flush failed: {e}"
        )
        raise
    pup_id = pupdate.id if pupdate is not None else "(no-pupdate)"
    if not repo:
        logger.warning(
            f"[automation {automation.id}] run_agent_job created AgentJob "
            f"{job_id} (kind={kind!r}) with NO scope_repo — pupdate={pup_id}, "
            f"automation.scope_repo={automation.scope_repo!r}, "
            f"pupdate.extra.repo="
            f"{((pupdate.extra or {}).get('repo') if pupdate is not None else None)!r}"
        )
    else:
        logger.info(
            f"[automation {automation.id}] spawned AgentJob {job_id} "
            f"(kind={kind!r}, scope_repo={repo!r}) pupdate={pup_id}"
        )
    return {"job_id": job_id, "kind": "run_agent_job", "status": job.status}


def _act_create_task(automation, config, pupdate=None, context=None):
    """Create a user-owed Task.

    Use this when the automation surfaces work the *user* needs to do
    (a todo, a bug, something they personally own). For pack-owned one-
    shot runs (cartograph, investigate, skills), use `run_agent_job`.

    Config:
      type: str           — todo | bug | feature | coding etc.
      title: str
      description: str
      priority: str
      repo: str           — falls back to chain `service` when unset.
      auto_launch: bool   — when true and the task type is agent-runnable
                            (review / investigation / cartograph /
                            repo_analysis), immediately spawn a linked
                            AgentJob so the cycle's execute phase kicks
                            off an agent without waiting for manual
                            Assign. No-op on user-owed types (todo /
                            bug / feature) — there's no agent to launch.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route, is_ready

    ctx = context or {}
    task_id = f"task-{uuid.uuid4().hex[:10]}"
    pup_extra = (pupdate.extra or {}) if pupdate is not None else {}
    task_type = config.get("type") or "todo"
    repo = (
        config.get("repo")
        or automation.scope_repo
        or ctx.get("service")
        or ctx.get("repo")
        # Pupdate metadata is the authoritative source for review /
        # pr_review tasks fired by per-PR pollers. Without this fallback
        # the AgentJob's scope_repo lands as None and the cycle's
        # execute phase can't resolve a worktree.
        or pup_extra.get("repo")
        or pup_extra.get("repository")
        or ""
    )
    if not repo and task_type in ("review", "pr_review") and pupdate is not None:
        logger.warning(
            f"[automation {automation.id}] create_task fired for "
            f"{task_type} from pupdate {pupdate.id} with NO repo — "
            f"pupdate.extra keys: {sorted(pup_extra.keys()) or '(empty)'}"
        )
    task = Task(
        id=task_id,
        title=config.get("title") or automation.name,
        type=task_type,
        priority=config.get("priority") or "normal",
        status="new",
        extra={
            "description": config.get("description") or automation.description or "",
            "repo": repo,
            "from_automation": automation.id,
        },
        tags=["from_automation"],
    )
    db.session.add(task)
    db.session.flush()
    try:
        route(task)
    except Exception as e:
        logger.warning(
            f"[automation {automation.id}] route(task={task.id}) failed: {e}"
        )
    if not is_ready(task):
        task.status = "blocked"

    # Auto-launch: spawn a linked AgentJob so the cycle's execute
    # phase picks it up next tick. Mirrors the Stage-D pattern where
    # a review Task's spawn_jobs_for_tasks phase does the same thing
    # — just doing it inline here saves up to one cycle tick (~30s)
    # of latency when the user wants "create task and go."
    agent_runnable = {"review", "pr_review", "investigation", "repo_analysis", "cartograph"}
    if bool(config.get("auto_launch")):
        # Log the "auto_launch was on but I'm skipping" path explicitly
        # so the user can tell *why* the AgentJob they expected didn't
        # appear. Common causes:
        #   - task_type is the skill id "pr-review" instead of the task
        #     type "review" / "pr_review"
        #   - route() failed silently and assigned_agent_id is None
        if task.type not in agent_runnable:
            logger.warning(
                f"[automation {automation.id}] auto_launch skipped: "
                f"task.type={task.type!r} not in {sorted(agent_runnable)} "
                f"(use 'review' or 'pr_review' for PR review tasks; "
                f"'pr-review' is the skill id, not the task type)"
            )
        elif not task.assigned_agent_id:
            logger.warning(
                f"[automation {automation.id}] auto_launch skipped: "
                f"task {task.id} has no assigned agent — route() couldn't "
                f"resolve one for repo={repo!r}, role={task.type!r}"
            )
    if bool(config.get("auto_launch")) and task.type in agent_runnable and task.assigned_agent_id:
        from planet_maiko.models.agent_job import AgentJob
        job_extra = {
            "from_automation": automation.id,
            "from_task": task.id,
        }
        if pupdate is not None:
            job_extra["triggered_by_pupdate"] = pupdate.id
        chain_ids = ctx.get("pupdate_ids") or []
        if chain_ids:
            job_extra["triggered_by_pupdates"] = list(chain_ids)
        job = AgentJob(
            id=f"job-{uuid.uuid4().hex[:10]}",
            kind=task.type,
            title=task.title,
            description=(task.extra or {}).get("description") or "",
            scope_repo=(task.extra or {}).get("repo") or None,
            priority=task.priority or "normal",
            created_by="automation",
            automation_id=automation.id,
            source_task_id=task.id,
            agent_profile_id=task.assigned_agent_id,
            requires_approval=False,
            approved_by="auto",
            approved_at=datetime.now(timezone.utc),
            status="queued",
            extra=job_extra,
        )
        db.session.add(job)
        return {"task_id": task_id, "job_id": job.id, "kind": "create_task", "auto_launched": True}

    return {"task_id": task_id, "kind": "create_task"}


def _act_spawn_agent_job_from_pupdate(automation, config, pupdate=None, context=None):
    """Pupdate-scope sibling of run_agent_job — spawns an AgentJob
    using the matched pupdate for context (repo, title).

    Config:
      kind, ask_first, description override, priority.
    """
    if pupdate is None:
        return {"skipped": "spawn_agent_job_from_pupdate requires pupdate context"}
    from planet_maiko.models.agent_job import AgentJob

    ask_first = bool(config.get("ask_first", False))
    kind = config.get("kind") or "investigation"
    repo = (pupdate.extra or {}).get("repo") or automation.scope_repo or None
    title = config.get("title") or f"{kind} triggered by {pupdate.type}"
    description = config.get("description") or pupdate.body or pupdate.title or ""
    priority = config.get("priority") or pupdate.priority or "normal"
    specialty_id = (config.get("specialty_id") or "").strip() or None

    extra = {
        "from_automation": automation.id,
        "triggered_by_pupdate": pupdate.id,
    }
    snap = _pupdate_snapshot(pupdate)
    if snap:
        extra["pupdate_snapshot"] = snap
    chain_ids = (context or {}).get("pupdate_ids") or []
    if chain_ids and chain_ids != [pupdate.id]:
        extra["triggered_by_pupdates"] = list(chain_ids)
    if specialty_id:
        extra["specialty_id"] = specialty_id

    # Ask-first → Memo, not a pending_approval AgentJob. Same
    # rationale as _act_run_agent_job: we don't mint jobs until the
    # user has decided they'll actually run.
    if ask_first:
        from planet_maiko.brain.memos import create_memo
        memo_extra = {
            **extra,
            "job_spec": {
                "kind": kind,
                "title": title,
                "description": description,
                "scope_repo": repo,
                "priority": priority,
                "automation_id": automation.id,
            },
        }
        memo = create_memo(
            kind="job_approval",
            category="offer",
            title=title,
            body=description or None,
            priority=priority,
            cta_label="Approve",
            cta_action="approve",
            source_pupdate_id=pupdate.id,
            extra=memo_extra,
        )
        db.session.flush()
        return {
            "memo_id": memo.id,
            "kind": "spawn_agent_job_from_pupdate",
            "status": "awaiting_approval",
        }

    job_id = f"job-{uuid.uuid4().hex[:10]}"
    job = AgentJob(
        id=job_id,
        kind=kind,
        title=title,
        description=description,
        scope_repo=repo,
        priority=priority,
        created_by="automation",
        automation_id=automation.id,
        requires_approval=False,
        status="queued",
        approved_by="auto",
        approved_at=datetime.now(timezone.utc),
        extra=extra,
    )
    db.session.add(job)
    # Flush early so any DB error surfaces here (with a stack we can
    # see) rather than silently rolling back at the cycle's final
    # commit. Without this, a SQLite lock or constraint violation
    # downstream eats the AgentJob, the pupdate still gets marked
    # processed, and we end up with a "pupdate processed but no job"
    # mystery in the DB.
    try:
        db.session.flush()
    except Exception as e:
        logger.warning(
            f"[automation {automation.id}] AgentJob flush failed for "
            f"pupdate {pupdate.id}: {e}"
        )
        raise
    if not repo:
        logger.warning(
            f"[automation {automation.id}] spawn_agent_job_from_pupdate "
            f"created AgentJob {job_id} (kind={kind!r}) with NO scope_repo "
            f"— pupdate {pupdate.id} extra keys: "
            f"{sorted((pupdate.extra or {}).keys()) or '(empty)'}"
        )
    else:
        logger.info(
            f"[automation {automation.id}] spawned AgentJob {job_id} "
            f"(kind={kind!r}, scope_repo={repo!r}) from pupdate {pupdate.id}"
        )
    return {"job_id": job_id, "kind": "spawn_agent_job_from_pupdate", "status": job.status}


def _act_dismiss_pupdate(automation, config, pupdate=None, context=None):
    if pupdate is None:
        return {"skipped": "dismiss_pupdate requires pupdate context"}
    pupdate.dismissed = True
    pupdate.dismissed_at = datetime.now(timezone.utc)
    return {"kind": "dismiss_pupdate", "pupdate_id": pupdate.id}


def _act_create_task_from_pupdate(automation, config, pupdate=None, context=None):
    """Rule-style create-a-task: use the pupdate's title/priority as the
    task seed, letting config override task_type and task_priority.
    Mirrors _execute_create_task in the old processor.

    Dedupes on (url, type) — GitHub's review-request source_id includes
    the head SHA so every push to an open PR creates a fresh pupdate,
    which used to spawn a new task each time. If an open task of the
    same type already points at this PR, we skip and just link the new
    pupdate to the existing task via source_pupdate_id so the thread
    of activity stays together.
    """
    if pupdate is None:
        return {"skipped": "create_task_from_pupdate requires pupdate context"}
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route, is_ready
    import uuid as _uuid

    task_type = config.get("task_type") or pupdate.type
    task_priority = config.get("task_priority") or pupdate.priority or "normal"

    if pupdate.url:
        existing = (
            Task.query
            .filter(Task.url == pupdate.url)
            .filter(Task.type == task_type)
            .filter(Task.status.notin_(["done", "cancelled"]))
            .first()
        )
        if existing:
            existing.source_pupdate_id = pupdate.id
            existing.updated_at = datetime.now(timezone.utc)

            # Refresh task.extra from the new pupdate's metadata. If
            # the original pupdate had stale or missing fields (older
            # poller version, GitHub API hiccup), the new pupdate is
            # the more authoritative source and we want downstream
            # consumers (scope_for_task, build_task_prompt) to read
            # the current values. Only fill keys that are missing or
            # empty on the existing task — don't clobber user edits.
            new_pup_extra = pupdate.extra or {}
            existing_extra = dict(existing.extra or {})
            for key in ("repo", "linear_id", "identifier",
                        "linear_cycle_id", "linear_cycle_number",
                        "linear_cycle_name"):
                if new_pup_extra.get(key) and not existing_extra.get(key):
                    existing_extra[key] = new_pup_extra[key]
                    logger.info(
                        f"[automation {automation.id}] task {existing.id}: "
                        f"backfilled extra.{key}={new_pup_extra[key]!r} "
                        f"from pupdate {pupdate.id}"
                    )
            existing.extra = existing_extra

            # If the task was parked in "waiting" (user posted their
            # review, ball in author's court), a fresh re-request
            # means the author wants another look — flip back to
            # "new" so it reappears in What-I'd-start-with and the
            # cycle's spawn_jobs_for_tasks phase picks it up for a
            # new review pass. Tasks in "new"/"in_progress" stay as
            # they are.
            status_flipped = False
            if existing.status == "waiting":
                existing.status = "new"
                status_flipped = True
                # Clear the old worktree pointer so the cycle's prep
                # phase re-preps against the PR's current HEAD. The
                # previous worktree's on an old SHA; the fresh review
                # pass needs the new commits the author just pushed.
                # cleanup_task_worktree tears down the old dir; the
                # prep phase rebuilds.
                extra = dict(existing.extra or {})
                wp = extra.get("working_path")
                branch = extra.get("branch")
                if wp and branch and ".maiko-worktrees" in wp:
                    try:
                        from planet_maiko.agents.coding_agent import cleanup
                        cleanup(wp, branch)
                    except Exception as e:
                        logger.debug(
                            f"[automation {automation.id}] "
                            f"stale worktree cleanup skipped: {e}"
                        )
                extra.pop("working_path", None)
                extra.pop("branch", None)
                extra.pop("session_id", None)
                existing.extra = extra
            return {
                "kind": "create_task_from_pupdate",
                "task_id": existing.id,
                "pupdate_id": pupdate.id,
                "deduped": True,
                "status_flipped": status_flipped,
            }

    task_id = f"task-{_uuid.uuid4().hex[:10]}"
    pup_extra = pupdate.extra or {}
    repo = pup_extra.get("repo") or ""
    if not repo and task_type in ("review", "pr_review"):
        # Review tasks without a repo can't resolve a worktree later —
        # surface this loudly at creation time rather than discovering
        # it three cycles later when prepare() fails.
        logger.warning(
            f"[automation {automation.id}] "
            f"creating {task_type} task from pupdate {pupdate.id} "
            f"with NO repo — pupdate.extra keys: "
            f"{sorted((pup_extra or {}).keys()) or '(empty)'}"
        )
    extra = {
        "description": pupdate.body or "",
        "repo": repo,
        "from_automation": automation.id,
    }
    snap = _pupdate_snapshot(pupdate)
    if snap:
        extra["pupdate_snapshot"] = snap
    # Carry integration-specific identifiers through so downstream
    # sync (e.g. Linear status mirroring) can find the task by its
    # source id. Narrow list — don't leak unrelated pupdate fields.
    for key in (
        "linear_id", "identifier",
        "linear_cycle_id", "linear_cycle_number", "linear_cycle_name",
    ):
        if pup_extra.get(key) is not None:
            extra[key] = pup_extra[key]
    task = Task(
        id=task_id,
        title=pupdate.title,
        type=task_type,
        priority=task_priority,
        status="new",
        source_pupdate_id=pupdate.id,
        url=pupdate.url,
        tags=list(pupdate.tags or []),
        extra=extra,
    )
    db.session.add(task)
    db.session.flush()
    try:
        route(task)
    except Exception as e:
        # route() lazy-spawns an agent; if that fails the task is
        # left without an assigned agent and the spawn-jobs phase
        # will never pick it up — silently. Surface the failure so
        # we can see why the chain stalled.
        logger.warning(
            f"[automation {automation.id}] route(task={task.id}) failed: {e}"
        )
    if not task.assigned_agent_id and task_type in ("review", "pr_review"):
        logger.warning(
            f"[automation {automation.id}] task {task.id} ({task_type}) "
            f"created without an assigned agent — spawn_jobs_for_tasks "
            f"will skip it. Check route()/maybe_spawn for repo={repo!r}."
        )
    if not is_ready(task):
        task.status = "blocked"
    return {"kind": "create_task_from_pupdate", "task_id": task_id, "pupdate_id": pupdate.id}


def _act_complete_linked_task(automation, config, pupdate=None, context=None):
    """Close review / coding tasks whose url matches this pupdate's url.
    Replaces the old ACTION_COMPLETE_TASK in rules.py — same cleanup
    semantics, now living inside the Automation engine.

    Also dismisses every un-dismissed pupdate pointing at the same URL
    so the overview and ReviewQueue stop surfacing "reviewer requested"
    / "changes requested" cards for a PR that's already closed. Without
    this the pupdates linger for their full 24h freshness window and
    Maiko keeps mentioning them in the narrative.
    """
    if pupdate is None or not pupdate.url:
        return {"skipped": "no url"}
    from planet_maiko.models.task import Task
    from planet_maiko.models.pupdate import Pupdate

    closed_review = 0
    closed_coding = 0
    dismissed_linked = 0
    # Review tasks hold onto their worktree through the "review" status
    # (so the user can load the diff inline) — once the PR is merged or
    # approved and we close the task, the worktree has no remaining job.
    # Clean it up here alongside coding tasks below.
    review_tasks = Task.query.filter(
        Task.url == pupdate.url,
        Task.type.in_(["review", "pr_review"]),
        Task.status.in_(["new", "in_progress", "review"]),
    ).all()
    for t in review_tasks:
        t.status = "done"
        t.updated_at = datetime.now(timezone.utc)
        closed_review += 1
        branch = (t.extra or {}).get("branch")
        wp = (t.extra or {}).get("working_path")
        if branch and wp and ".maiko-worktrees" in wp:
            try:
                from planet_maiko.agents.coding_agent import cleanup
                cleanup(wp, branch)
            except Exception as e:
                logger.debug(f"[automation {automation.id}] review worktree cleanup failed: {e}")

    coding_tasks = Task.query.filter(
        Task.status.in_(["new", "in_progress", "in_review"]),
    ).all()
    for t in coding_tasks:
        if t.url == pupdate.url or (t.extra or {}).get("pr_url") == pupdate.url:
            t.status = "done"
            t.updated_at = datetime.now(timezone.utc)
            closed_coding += 1
            # Worktree cleanup for Maiko-owned coding agents
            branch = (t.extra or {}).get("branch")
            wp = (t.extra or {}).get("working_path")
            if branch and wp and ".maiko-worktrees" in wp:
                try:
                    from planet_maiko.agents.coding_agent import cleanup
                    cleanup(wp, branch)
                except Exception as e:
                    logger.debug(f"[automation {automation.id}] worktree cleanup failed: {e}")

    # Dismiss all pupdates pointing at this URL (review_requested,
    # changes_requested, approved, merged, etc.) so the overview and
    # ReviewQueue stop showing cards for a PR that's closed. The
    # triggering pupdate itself is included — once we've acted on it,
    # it has no further value sitting in the inbox.
    linked_pupdates = (
        Pupdate.query
        .filter(Pupdate.url == pupdate.url)
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .all()
    )
    now = datetime.now(timezone.utc)
    for p in linked_pupdates:
        p.dismissed = True
        p.dismissed_at = now
        dismissed_linked += 1

    return {
        "kind": "complete_linked_task",
        "review_tasks_closed": closed_review,
        "coding_tasks_closed": closed_coding,
        "pupdates_dismissed": dismissed_linked,
    }


def _act_skip(automation, config, pupdate=None, context=None):
    """Explicit no-op — useful for 'mark this pattern as handled,
    don't dispatch anything.' Pairs with pupdate-scope automations
    where you want the first-match behavior to claim the pupdate but
    not produce any side effects."""
    return {"kind": "skip"}


def _interpolate(template, pupdate=None, context=None):
    """Substitute {pupdate_title}, {pupdate_body}, {pupdate_url},
    {repo}, {task_title} into a notify template.

    Tokens that can't be resolved pass through as the empty string;
    we'd rather the user see "PR  was reviewed" with a visual gap
    than a crash or a literal "{pupdate_title}" showing up in the
    notification body. Missing-data blanks are a clearer bug signal.
    """
    if not template:
        return ""
    ctx = context or {}
    mapping = {
        "pupdate_title": getattr(pupdate, "title", "") or "" if pupdate else "",
        # 2000 chars is enough for most PR descriptions / incident
        # bodies / Linear issue bodies to fit without cutting context
        # the user is trying to read.
        "pupdate_body": (getattr(pupdate, "body", "") or "")[:2000] if pupdate else "",
        "pupdate_url": getattr(pupdate, "url", "") or "" if pupdate else "",
        "repo": (ctx.get("repo")
                 or ctx.get("service")
                 or (getattr(pupdate, "extra", {}) or {}).get("repo")
                 or ""),
        "task_title": ctx.get("task_title", ""),
    }
    out = template
    for key, value in mapping.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def _pupdate_snapshot(pupdate):
    """Extract a pupdate's full surface for downstream consumers (memos,
    agent jobs, task extras). Includes the raw `extra` blob — different
    skills key off different metadata fields (pr-review needs `url`;
    investigate wants `body`; correlator-style skills might key on tags
    or extra.repo / extra.pr_number), so plumb it all and let the
    consumer pick.
    """
    if pupdate is None:
        return None
    body = getattr(pupdate, "body", None) or ""
    return {
        "id": getattr(pupdate, "id", None),
        "type": getattr(pupdate, "type", None),
        "title": (getattr(pupdate, "title", None) or "")[:300],
        # Full body here — the memo's top-level body field may have
        # been templated/truncated by the user's copy; this keeps the
        # raw text available for context.
        "body": body[:4000],
        "url": getattr(pupdate, "url", None),
        "source": getattr(pupdate, "source", None),
        "priority": getattr(pupdate, "priority", None),
        "tags": list(getattr(pupdate, "tags", None) or []),
        "timestamp": (
            pupdate.timestamp.isoformat()
            if getattr(pupdate, "timestamp", None) and hasattr(pupdate.timestamp, "isoformat")
            else None
        ),
        "extra": dict(getattr(pupdate, "extra", None) or {}),
    }


def format_pupdate_for_context(snapshot):
    """Render a pupdate snapshot dict as a markdown block for skill
    prompts. Ships every field — skills decide what to use. Returns
    an empty string when snapshot is None / empty so callers can
    unconditionally splice the result into a `{context}` placeholder.
    """
    if not snapshot:
        return ""
    lines = ["### Triggered by pupdate"]
    for key, label in (
        ("type", "Type"),
        ("source", "Source"),
        ("title", "Title"),
        ("url", "URL"),
        ("priority", "Priority"),
        ("timestamp", "Created"),
    ):
        value = snapshot.get(key)
        if value:
            lines.append(f"{label}: {value}")
    tags = snapshot.get("tags") or []
    if tags:
        lines.append(f"Tags: {', '.join(str(t) for t in tags)}")
    extra = snapshot.get("extra") or {}
    if extra:
        # Flatten one level — pupdate.extra is mostly key/value scalars
        # (repo, pr_number, head_sha, identifier, etc.). For nested
        # values, fall back to JSON so the structure stays readable.
        import json as _json
        lines.append("Metadata:")
        for k, v in extra.items():
            if isinstance(v, (dict, list)):
                v_str = _json.dumps(v, default=str)
            else:
                v_str = str(v)
            lines.append(f"  {k}: {v_str}")
    body = (snapshot.get("body") or "").strip()
    if body:
        lines.append("")
        lines.append("Body:")
        lines.append(body)
    return "\n".join(lines)


def _act_notify(automation, config, pupdate=None, context=None):
    """Emit a user-facing notification Memo.

    The Home page's NotificationsPane surfaces kind=notification memos
    as dismissable cards. Useful when you want to be told something
    fired without routing a task or spawning an agent — e.g. "notify
    me when a PR approval lands from someone outside my team" or
    "notify me when CI has been red for 30 minutes."

    Config fields (all optional except title):
      title:    short headline (required). Supports {pupdate_title}.
      body:     longer markdown (optional). Supports {pupdate_body}.
      priority: normal | high | urgent. Default normal.
      url:      optional click-through. Supports {pupdate_url}.
    """
    from planet_maiko.brain.memos import create_memo

    # Default: use the triggering pupdate's title. Matches what
    # _act_spawn_agent_job_from_pupdate does. Leaving the field blank
    # in the editor silently dropped every notify_me fire before this,
    # which was its own silent bug — by the time you noticed nothing
    # was showing up, the automation's fire_count was happily
    # incrementing and there was no trace in the memos table.
    title_template = (config.get("title") or "").strip()
    if not title_template:
        if pupdate is not None:
            title_template = "{pupdate_title}"
        else:
            title_template = automation.name or "Notification"
    title = _interpolate(title_template, pupdate=pupdate, context=context)
    # _interpolate can still return "" if the pupdate had an empty
    # title AND the template was just {pupdate_title} — fall back one
    # more step so the memo always has something rather than being
    # filed under "(notification)" in the UI.
    if not title.strip():
        title = automation.name or "Notification"
    body = _interpolate(config.get("body") or "", pupdate=pupdate, context=context)
    url_template = config.get("url") or (pupdate.url if pupdate else "")
    url = _interpolate(url_template, pupdate=pupdate, context=context) or None
    priority = (config.get("priority") or "normal").lower()
    if priority not in ("low", "normal", "high", "urgent"):
        priority = "normal"

    memo_extra = {
        "from_automation": automation.id,
        "triggered_by_pupdate": pupdate.id if pupdate else None,
    }
    snap = _pupdate_snapshot(pupdate)
    if snap:
        memo_extra["pupdate_snapshot"] = snap

    memo = create_memo(
        kind="notification",
        category="info",
        title=title[:200],
        body=body or None,
        url=url,
        priority=priority,
        source_pupdate_id=pupdate.id if pupdate else None,
        extra=memo_extra,
    )
    db.session.flush()
    return {"kind": "notify_me", "memo_id": memo.id, "title": title[:80]}


ACTIONS = {
    # Cycle-scope
    "run_agent_job": _act_run_agent_job,
    "create_task": _act_create_task,
    "notify_me": _act_notify,
    # Pupdate-scope (require context.pupdate to operate).
    "spawn_agent_job_from_pupdate": _act_spawn_agent_job_from_pupdate,
    "dismiss_pupdate": _act_dismiss_pupdate,
    "create_task_from_pupdate": _act_create_task_from_pupdate,
    "complete_linked_task": _act_complete_linked_task,
    "skip": _act_skip,
}