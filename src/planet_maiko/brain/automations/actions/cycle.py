"""Cycle-scoped action handlers — fire once per cycle when their
condition matches. They don't require a triggering pupdate (though
some can carry one through context for snapshot purposes).
"""

import logging
import uuid
from datetime import datetime, timezone

from planet_maiko.database import db

from ._helpers import _interpolate, _pupdate_snapshot

logger = logging.getLogger(__name__)


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
    #   4. Wildcard condition's `repo` (overview_stale picked a specific
    #      repo this cycle)
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
    # silently rolled back at the cycle's final commit.
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


def _act_skip(automation, config, pupdate=None, context=None):
    """Explicit no-op — useful for 'mark this pattern as handled,
    don't dispatch anything.' Pairs with pupdate-scope automations
    where you want the first-match behavior to claim the pupdate but
    not produce any side effects."""
    return {"kind": "skip"}


def _act_notify(automation, config, pupdate=None, context=None):
    """Emit a user-facing notification Memo.

    The Home page's NotificationsPane surfaces kind=notification memos
    as dismissable cards. Useful when you want to be told something
    fired without routing a task or spawning an agent — e.g. "notify
    me when a PR approval lands from someone outside my team" or
    "notify me when CI has been red for 30 minutes."

    Config fields (all optional):
      title:    short headline. Defaults to {pupdate_title}.
      body:     longer markdown. Defaults to {pupdate_body} so the
                user sees the full pupdate content in the memo card
                without needing to click through.
      priority: normal | high | urgent. Default normal.
      url:      optional click-through. Supports {pupdate_url}.

    Idempotency: if this automation already minted a notification memo
    for this pupdate (matched by source_pupdate_id + extra.from_automation),
    return the existing memo instead of creating a duplicate. Without
    this, a cycle-scope automation re-firing on the same pupdate
    across multiple cycles produced N memos for the same event.
    """
    from planet_maiko.brain.memos import create_memo
    from planet_maiko.models.memo import Memo

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
    if not title.strip():
        title = automation.name or "Notification"

    # Default body = pupdate body. Same reasoning as the title — most
    # notify_me automations want the user to see the full pupdate
    # content; making them re-type "{pupdate_body}" every time is
    # busywork, and an empty body produces a card with just the title
    # which loses context. Explicit "" in the editor still wins
    # (someone who really wants a title-only card sets body="" with
    # an empty string, not by leaving it blank).
    body_template = config.get("body")
    if body_template is None:
        body_template = "{pupdate_body}" if pupdate is not None else ""
    body = _interpolate(body_template, pupdate=pupdate, context=context)

    url_template = config.get("url") or (pupdate.url if pupdate else "")
    url = _interpolate(url_template, pupdate=pupdate, context=context) or None
    priority = (config.get("priority") or "normal").lower()
    if priority not in ("low", "normal", "high", "urgent"):
        priority = "normal"

    # Idempotency: same (automation, pupdate) shouldn't mint a second
    # memo. Scoped to non-dismissed memos so a user-dismissed memo
    # doesn't permanently suppress legitimate re-firing on a future
    # similar pupdate (the source_pupdate_id gates that — different
    # pupdate id = new memo).
    if pupdate is not None:
        existing = (
            Memo.query
            .filter(Memo.kind == "notification")
            .filter(Memo.source_pupdate_id == pupdate.id)
            .filter(Memo.status.in_(("pending", "seen", "actioned")))
            .all()
        )
        for m in existing:
            if (m.extra or {}).get("from_automation") == automation.id:
                return {
                    "kind": "notify_me",
                    "memo_id": m.id,
                    "title": (m.title or "")[:80],
                    "duplicate": True,
                }

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
