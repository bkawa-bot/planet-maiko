"""One-shot data migrations called at boot from app.py.

Each function is idempotent — they all match on a marker (name,
status, action kind) so re-running on already-migrated rows is a
no-op. Once every existing install has run them, individual ones
can be deprecated by leaving them as no-ops for a release before
removal.
"""

import logging

from planet_maiko.database import db
from planet_maiko.models.automation import Automation

logger = logging.getLogger(__name__)




# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

# Pupdate types the old Ops-chain seeds referenced. Used by the
# migration to archive existing rows that still reference them, so
# users don't see rows that can never fire.
_RETIRED_OPS_TYPES = {
    "incident",
    "error_spike",
    "deploy_rollback",
    "deploy_blocked",
    "deploy_stuck",
    "batch_job_failing",
}


def migrate_archive_retired_chain_seeds():
    """Archive seeded incident-chain automations that reference
    pupdate types no default poller emits.

    The original chain seeds assumed an ops signal source (Datadog,
    Sentry, PagerDuty) emitting `incident` / `error_spike` /
    `deploy_*` / `batch_job_failing`. Maiko doesn't ship those
    pollers — so the chains sat on the Automations page forever
    unable to fire. Archive them so the list only shows rules that
    can actually trigger on this install.

    Only touches seed-created rows. User-authored automations that
    reference retired types are left alone — if the user wrote it,
    they might have a plugin in flight that emits the type.
    """
    legacy = (
        Automation.query
        .filter(Automation.created_by == "seed")
        .filter(Automation.status == "active")
        .all()
    )
    archived = 0
    for a in legacy:
        # A chain seed references its required types in when[].config.types.
        retired_refs = False
        for cond in (a.when or []):
            if cond.get("kind") != "pupdate_chain":
                continue
            types = (cond.get("config") or {}).get("types") or []
            if any(t in _RETIRED_OPS_TYPES for t in types):
                retired_refs = True
                break
        if not retired_refs:
            continue
        a.status = "archived"
        archived += 1
    if archived:
        db.session.commit()
        logger.info(
            f"[automations] archived {archived} seeded chain(s) that "
            "referenced retired ops pupdate types"
        )
    return archived


def migrate_per_repo_overview_watches():
    """Archive old per-repo `Keep <repo>'s overview current` seeds once
    the wildcard version is in place.

    Runs on every startup; a no-op after the first successful pass.
    We archive rather than delete so the user's fire history stays
    queryable. Only touches rows the seed code created — anything the
    user hand-authored or edited stays put.
    """
    wildcard = (
        Automation.query
        .filter(Automation.name == "Keep repo overviews current")
        .filter(Automation.created_by == "seed")
        .first()
    )
    if wildcard is None:
        return 0  # wildcard isn't in place yet — safer to leave old rows active

    legacy = (
        Automation.query
        .filter(Automation.created_by == "seed")
        .filter(Automation.status == "active")
        .filter(Automation.scope_repo.isnot(None))
        .all()
    )
    archived = 0
    for a in legacy:
        has_overview_watch = any(
            t.get("kind") == "overview_stale" for t in (a.when or [])
        )
        if not has_overview_watch:
            continue
        a.status = "archived"
        archived += 1
    if archived:
        db.session.commit()
        logger.info(
            f"[automations] archived {archived} per-repo overview "
            "watch(es) superseded by the wildcard seed"
        )
    return archived


def migrate_scheduled_skills():
    """One-time import of CustomSkills that have a non-null
    schedule_interval_minutes into Automations (cadence + run_skill).

    Idempotent: clears schedule_interval_minutes on the source skill
    after migration so the skill runner (now deleted) wouldn't also
    fire, and subsequent boots see no more rows to migrate.
    """
    try:
        from planet_maiko.models.custom_skill import CustomSkill
    except Exception:
        return 0

    skills = (
        CustomSkill.query
        .filter(CustomSkill.schedule_interval_minutes.isnot(None))
        .filter(CustomSkill.schedule_interval_minutes > 0)
        .all()
    )
    if not skills:
        return 0

    migrated = 0
    for s in skills:
        automation = Automation(
            name=f"{s.name} on a schedule",
            description=(
                f"Scheduled run of the {s.name} skill every "
                f"{s.schedule_interval_minutes} minute(s). "
                + (s.description or "")
            ).strip(),
            when=[{
                "kind": "cadence",
                "config": {"interval_minutes": int(s.schedule_interval_minutes)},
            }],
            when_logic="all",
            then=[{
                "kind": "run_agent_job",
                "config": {
                    "ask_first": False,
                    "kind": s.id,  # skill name = job kind
                    "title": s.name,
                },
            }],
            status="active",
            created_by="seed",
            cooldown_days=0,  # cadence condition is the timing source
        )
        db.session.add(automation)
        # Clear the legacy schedule so the old runner (if it still
        # somehow got called) wouldn't double-fire.
        s.schedule_interval_minutes = None
        migrated += 1

    if migrated:
        db.session.commit()
        logger.info(f"[automations] migrated {migrated} scheduled CustomSkill(s) to Automation")
    return migrated


PACK_OWNED_KINDS = {
    # Pack-owned one-shot runs. Any automation whose create_task points
    # at one of these types really means "spawn an AgentJob" — migration
    # rewrites accordingly. Add new skills here as they're registered.
    "cartograph", "investigation", "repo_analysis",
    "brainstorm", "checkin",
    "plan", "team", "verify", "home-overview", "theme-designer", "pr-review",
    "investigate",
}


def migrate_legacy_action_kinds():
    """Rewrite legacy action kinds into the current set. Idempotent.

    Today's transitions:
      - `propose` → `create_task(ask_first=true)` (Stage 5 change)
      - `run_skill` → `create_task(ask_first=false)` (Stage 5 change)
      - `create_task(type in PACK_OWNED_KINDS)` → `run_agent_job` (this stage)
      - `nudge` → dropped (action gone; existing rows get an empty then[]
        which the engine treats as a no-op match — user can delete)
    """
    rewrote = 0
    rows = (
        Automation.query
        .filter(Automation.status != "archived")
        .all()
    )
    for a in rows:
        new_then = []
        changed = False
        for action in (a.then or []):
            kind = action.get("kind")
            cfg = action.get("config") or {}
            if kind == "propose":
                draft = cfg.get("draft") or {}
                draft_type = draft.get("type") or "todo"
                if draft_type in PACK_OWNED_KINDS:
                    new_then.append({
                        "kind": "run_agent_job",
                        "config": {
                            "ask_first": True,
                            "kind": draft_type,
                            "title": draft.get("title") or "",
                            "priority": draft.get("priority") or "normal",
                            "scope_repo": draft.get("repo") or "",
                            "description": draft.get("description") or "",
                        },
                    })
                else:
                    new_then.append({
                        "kind": "create_task",
                        "config": {
                            "type": draft_type,
                            "title": draft.get("title") or "",
                            "priority": draft.get("priority") or "normal",
                            "repo": draft.get("repo") or "",
                            "description": draft.get("description") or "",
                        },
                    })
                changed = True
            elif kind == "run_skill":
                new_then.append({
                    "kind": "run_agent_job",
                    "config": {
                        "ask_first": False,
                        "kind": cfg.get("skill_name") or "todo",
                        "title": cfg.get("title") or "",
                        "priority": cfg.get("priority") or "normal",
                        "scope_repo": cfg.get("scope_repo") or "",
                        "description": cfg.get("input") or "",
                    },
                })
                changed = True
            elif kind == "create_task":
                # Post-Stage-5 shape. Split further: create_task stays
                # for user-owed types; pack-owned types become run_agent_job.
                task_type = cfg.get("type") or "todo"
                if task_type in PACK_OWNED_KINDS:
                    new_then.append({
                        "kind": "run_agent_job",
                        "config": {
                            "ask_first": bool(cfg.get("ask_first", False)),
                            "kind": task_type,
                            "title": cfg.get("title") or "",
                            "priority": cfg.get("priority") or "normal",
                            "scope_repo": cfg.get("repo") or "",
                            "description": cfg.get("description") or "",
                        },
                    })
                    changed = True
                else:
                    new_then.append(action)
            elif kind == "nudge":
                # Nudge retired — drop the action. If the user wanted
                # a reminder, they can replace it with a create_task.
                changed = True
                continue
            else:
                new_then.append(action)
        if changed:
            a.then = new_then
            rewrote += 1
    if rewrote:
        db.session.commit()
        logger.info(f"[automations] rewrote {rewrote} legacy action kind(s)")
    return rewrote


def migrate_tasks_to_agent_jobs():
    """One-time migration: Task rows with pack-owned types become
    AgentJob rows. Also migrates agent_proposal pupdates that were
    emitted by automations (had automation_id in extra) into
    pending-approval AgentJobs.

    Idempotent — subsequent boots find no more matching Tasks / pupdates.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_job import AgentJob

    migrated_tasks = 0
    candidates = (
        Task.query
        .filter(Task.type.in_(list(PACK_OWNED_KINDS)))
        .all()
    )
    for t in candidates:
        extra = t.extra or {}
        automation_id = extra.get("from_automation")
        status_map = {
            "new": "queued",
            "blocked": "queued",
            "in_progress": "running",
            "in_review": "running",
            "done": "done",
            "cancelled": "cancelled",
        }
        job_status = status_map.get(t.status, "queued")
        job = AgentJob(
            id=t.id if t.id.startswith("job-") else f"job-{uuid.uuid4().hex[:10]}",
            kind=t.type,
            title=t.title,
            description=extra.get("description") or "",
            scope_repo=extra.get("repo") or extra.get("repository"),
            priority=t.priority or "normal",
            created_by="automation" if automation_id else "user",
            automation_id=automation_id,
            status=job_status,
            agent_profile_id=t.assigned_agent_id,
            worktree_path=extra.get("working_path"),
            branch=extra.get("branch"),
            requires_approval=False,
            approved_at=datetime.now(timezone.utc),
            approved_by="auto",
            artifact=extra.get("artifact"),
            extra={k: v for k, v in extra.items() if k not in (
                "description", "repo", "repository", "working_path",
                "branch", "from_automation", "artifact",
            )},
        )
        # Preserve the original task ID if it was already job-shaped,
        # otherwise mint a new one and delete the stale task row.
        db.session.add(job)
        db.session.delete(t)
        migrated_tasks += 1

    # Pending agent_proposal pupdates with automation_id become
    # pending-approval AgentJobs.
    migrated_proposals = 0
    proposals = (
        Pupdate.query
        .filter(Pupdate.type == "agent_proposal")
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .all()
    )
    for p in proposals:
        extra = p.extra or {}
        automation_id = extra.get("automation_id")
        if not automation_id:
            continue  # agent-authored proposals: keep as pupdates
        draft = extra.get("draft") or {}
        draft_type = draft.get("type") or "todo"
        if draft_type not in PACK_OWNED_KINDS:
            continue  # user-owed proposal, keep as pupdate
        job_id = f"job-{uuid.uuid4().hex[:10]}"
        job = AgentJob(
            id=job_id,
            kind=draft_type,
            title=draft.get("title") or p.title,
            description=draft.get("description") or p.body or "",
            scope_repo=draft.get("repo"),
            priority=draft.get("priority") or p.priority or "normal",
            created_by="automation",
            automation_id=automation_id,
            requires_approval=True,
            status="pending_approval",
        )
        db.session.add(job)
        p.dismissed = True
        p.dismissed_at = datetime.now(timezone.utc)
        migrated_proposals += 1

    if migrated_tasks or migrated_proposals:
        db.session.commit()
        logger.info(
            f"[agent_jobs] migrated {migrated_tasks} Task(s) + "
            f"{migrated_proposals} pending proposal(s) to AgentJob"
        )
    return migrated_tasks + migrated_proposals


