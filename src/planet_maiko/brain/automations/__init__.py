"""Automation engine — evaluates every active Automation row each brain
cycle and fires the action chain when conditions hold.

Replaces the AgentGoal autonomy module. Same pattern (condition-based
proposals), now user-editable and uniformly surfaced in the Automations
dashboard.

Supported condition kinds (keep this comment + CONDITIONS dict in sync):
  - cadence:         {interval_hours: int}
                     Fires every N hours. `last_fired_at` drives the clock.
  - overview_stale:  {repo: str, stale_days: int}
                     Matches when the 'overview' Insight for this repo is
                     missing or older than stale_days.
  - lora_missing:    {repo: str, min_learnings: int}
                     Matches when the repo has >= min_learnings active
                     Learning rows and no AgentProfile for that scope_repo
                     has an extra.adapter_path set.

Supported action kinds:
  - propose:         {draft: {title, type, priority, repo?, description?}}
                     Emits an agent_proposal pupdate that the user approves
                     to create a Task. Matches the current keep_overview
                     behavior.
  - nudge:           {title, body, url?}
                     Emits a low-priority maiko_nudge pupdate. The user's
                     click on the inbox item opens the url, then they
                     dismiss. No task created.
  - run_skill:       {skill_name, role?, input?, scope_repo?}
                     Spawns a one-shot task of type `skill_name` assigned
                     to a role-appropriate agent (cartographer for
                     cartograph, investigation for investigate, etc.).
                     Enters the same execute-phase path as manually
                     launched tasks.
  - create_task:     {title, type?, priority?, description?, repo?}
                     Creates a Task directly without the proposal step.
                     Use when the trigger is confident enough to not
                     need human approval (use sparingly).

Not yet implemented (future stages):
  - pupdate_match / pupdate_chain conditions (correlator replacement)
  - dismiss / mark_read actions (rules.py replacement)
  - LLM-composed proposal text

The engine never calls an LLM itself — all intelligence is in skills
that actions reference by name.
"""

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.automation import Automation
from planet_maiko.models.pupdate import Pupdate

logger = logging.getLogger(__name__)


def _safe_format(template, context):
    """Substitute {key} placeholders in a template string with values
    from `context`. Missing keys render as "(unknown)" rather than
    raising — automation text should degrade gracefully when upstream
    shape shifts, not crash the cycle.
    """
    if not template or not isinstance(template, str):
        return template
    if "{" not in template:
        return template
    try:
        class _Defaulting(dict):
            def __missing__(self, key):  # noqa: D401
                return "(unknown)"
        return template.format_map(_Defaulting(context or {}))
    except Exception:
        return template


# ---------------------------------------------------------------------------
# Condition evaluators — each returns True/False given the config dict.
# Raised exceptions are caught at the engine level; detectors shouldn't
# raise for "no match", they should return False.
# ---------------------------------------------------------------------------

def _cond_cadence(automation, config):
    # Native unit is minutes so scheduled skill migrations (which come
    # in at minute precision — 15, 30, 60, etc.) stay lossless.
    # interval_hours is accepted as a convenience alias.
    if "interval_minutes" in config:
        minutes = int(config["interval_minutes"])
    else:
        minutes = int(config.get("interval_hours", 24)) * 60
    last = automation.last_fired_at
    if last is None:
        return True  # never fired yet — fire this cycle
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last) >= timedelta(minutes=minutes)


def _cond_overview_stale(automation, config):
    from planet_maiko.models.insight import Insight
    repo = config.get("repo") or automation.scope_repo
    if not repo:
        return False
    stale_days = int(config.get("stale_days", 30))

    rows = (
        Insight.query
        .filter(Insight.repo_scope == repo)
        .filter(Insight.status == "active")
        .order_by(Insight.last_confirmed_at.desc())
        .limit(20)
        .all()
    )
    overview = next(
        (i for i in rows if "overview" in (i.tags or []) or "cartographer" in (i.tags or [])),
        None,
    )
    if overview is None:
        return True  # missing entirely == stale
    last_confirmed = overview.last_confirmed_at
    if last_confirmed is None:
        return True
    if last_confirmed.tzinfo is None:
        last_confirmed = last_confirmed.replace(tzinfo=timezone.utc)
    return last_confirmed < (datetime.now(timezone.utc) - timedelta(days=stale_days))


def _cond_lora_missing(automation, config):
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.agent_profile import AgentProfile

    repo = config.get("repo") or automation.scope_repo
    if not repo:
        return False
    min_learnings = int(config.get("min_learnings", 10))

    active_count = (
        Learning.query
        .filter(Learning.status == "active")
        .filter(Learning.scope_repo == repo)
        .count()
    )
    if active_count < min_learnings:
        return False
    for profile in AgentProfile.query.filter(AgentProfile.scope_repo == repo).all():
        if (profile.extra or {}).get("adapter_path"):
            return False
    return True


def _cond_pupdate_match(automation, config):
    """Fires when any non-dismissed pupdate of the given type(s) has
    arrived within the last `within_minutes` window.

    Config:
      types: list[str]        — pupdate type names (required)
      within_minutes: int     — lookback window, default 60
      require_actionable: bool — only match if pupdate.actionable

    Returns match + context {service, pupdate_ids, types, pupdate_id}
    where `service` is the matched pupdate's repo (first tag if no
    extra.repo) so actions can templatize "{service}".
    """
    types = config.get("types") or []
    if not types:
        return {"match": False}
    within = int(config.get("within_minutes", 60))
    require_actionable = bool(config.get("require_actionable", False))

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within)
    q = Pupdate.query.filter(
        Pupdate.timestamp >= cutoff,
        Pupdate.dismissed == False,  # noqa: E712
        Pupdate.type.in_(types),
    )
    if require_actionable:
        q = q.filter(Pupdate.actionable == True)  # noqa: E712
    match = q.order_by(Pupdate.timestamp.desc()).first()
    if match is None:
        return {"match": False}
    repo = (match.extra or {}).get("repo")
    if not repo and (match.tags or []):
        repo = (match.tags or [None])[0]
    return {
        "match": True,
        "context": {
            "service": repo or "",
            "types": [match.type],
            "pupdate_id": match.id,
            "pupdate_ids": [match.id],
            "title": match.title or "",
        },
    }


def _cond_pupdate_chain(automation, config):
    """Fires when ALL of `types` appear within `within_minutes`, grouped
    by the same key (service/repo). Replaces the correlator's
    CAUSE_CHAINS matching.

    Config:
      types: list[str]       — required chain of pupdate types
      within_minutes: int    — window, default 30
      group_by: "repo" | "tag" — how to group (default "repo")

    Returns match + context {service, types, pupdate_ids} where
    service is the shared group key (usually an org/repo).
    """
    types = config.get("types") or []
    if len(types) < 2:
        return {"match": False}
    within = int(config.get("within_minutes", automation.within_minutes or 30))
    group_by = config.get("group_by", "repo")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within)
    pupdates = (
        Pupdate.query
        .filter(Pupdate.timestamp >= cutoff)
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .filter(Pupdate.type.in_(types))
        .order_by(Pupdate.timestamp.asc())
        .all()
    )
    if not pupdates:
        return {"match": False}

    groups = defaultdict(lambda: {"types": set(), "pupdate_ids": []})
    for p in pupdates:
        if group_by == "tag":
            key = (p.tags or [None])[0]
        else:
            key = (p.extra or {}).get("repo")
            if not key and (p.tags or []):
                key = (p.tags or [None])[0]
        if not key:
            continue
        groups[key]["types"].add(p.type)
        groups[key]["pupdate_ids"].append(p.id)

    required = set(types)
    for service, data in groups.items():
        if data["types"].issuperset(required):
            return {
                "match": True,
                "context": {
                    "service": service,
                    "types": sorted(list(data["types"])),
                    "pupdate_ids": data["pupdate_ids"],
                },
            }
    return {"match": False}


CONDITIONS = {
    "cadence": _cond_cadence,
    "overview_stale": _cond_overview_stale,
    "lora_missing": _cond_lora_missing,
    "pupdate_match": _cond_pupdate_match,
    "pupdate_chain": _cond_pupdate_chain,
}


# ---------------------------------------------------------------------------
# Action dispatchers — each returns a short result dict for logging.
# ---------------------------------------------------------------------------

def _act_propose(automation, config):
    draft = config.get("draft") or {}
    if not draft.get("title"):
        return {"skipped": "proposal draft missing title"}

    repo = draft.get("repo") or automation.scope_repo or ""
    pupdate_id = f"automation-{automation.id}-{uuid.uuid4().hex[:8]}"
    tag = f"automation:{automation.id}"
    pupdate = Pupdate(
        id=pupdate_id,
        source="maiko",
        source_id=f"automation/{automation.id}",
        type="agent_proposal",
        priority=draft.get("priority", "low"),
        title=draft.get("title") or automation.name,
        body=draft.get("description") or automation.description or "",
        actionable=True,
        action_hint="Approve / dismiss",
        tags=["proposal", "from_maiko", tag],
        extra={
            "from_agent_id": None,
            "draft": {
                "title": draft.get("title"),
                "type": draft.get("type") or "todo",
                "priority": draft.get("priority") or "normal",
                "repo": repo,
                "description": draft.get("description") or automation.description or "",
            },
            "automation_id": automation.id,
        },
        brain_processed=True,
    )
    db.session.add(pupdate)
    return {"pupdate_id": pupdate_id, "kind": "propose"}


def _act_nudge(automation, config):
    pupdate_id = f"automation-nudge-{automation.id}-{uuid.uuid4().hex[:8]}"
    tag = f"automation:{automation.id}"
    pupdate = Pupdate(
        id=pupdate_id,
        source="maiko",
        source_id=f"automation/{automation.id}",
        type="maiko_nudge",
        priority=config.get("priority", "low"),
        title=config.get("title") or automation.name,
        body=config.get("body") or automation.description or "",
        url=config.get("url"),
        actionable=True,
        action_hint=config.get("action_hint", "Open"),
        tags=["nudge", "from_maiko", tag],
        extra={"automation_id": automation.id},
        brain_processed=True,
    )
    db.session.add(pupdate)
    return {"pupdate_id": pupdate_id, "kind": "nudge"}


def _act_create_task(automation, config):
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route, is_ready

    task_id = f"task-{uuid.uuid4().hex[:10]}"
    task = Task(
        id=task_id,
        title=config.get("title") or automation.name,
        type=config.get("type") or "todo",
        priority=config.get("priority") or "normal",
        status="new",
        extra={
            "description": config.get("description") or "",
            "repo": config.get("repo") or automation.scope_repo or "",
            "from_automation": automation.id,
        },
        tags=["from_automation"],
    )
    db.session.add(task)
    db.session.flush()
    route(task)
    if not is_ready(task):
        task.status = "blocked"
    return {"task_id": task_id, "kind": "create_task"}


def _act_run_skill(automation, config):
    # Shortcut over create_task: builds a task of the right `type` so
    # the cycle's execute-agent-tasks phase runs it as a one-shot skill.
    # `skill_name` maps to task.type (the execute phase picks role from
    # ONE_SHOT_ROLE_FOR_TYPE in brain_session.py).
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route

    skill_name = config.get("skill_name")
    if not skill_name:
        return {"skipped": "run_skill missing skill_name"}

    task_id = f"task-{uuid.uuid4().hex[:10]}"
    task = Task(
        id=task_id,
        title=config.get("title") or f"{skill_name}: {automation.name}",
        type=skill_name,
        priority=config.get("priority") or "normal",
        status="new",
        extra={
            "description": config.get("input") or automation.description or "",
            "repo": config.get("scope_repo") or automation.scope_repo or "",
            "from_automation": automation.id,
        },
        tags=["from_automation"],
    )
    db.session.add(task)
    db.session.flush()
    route(task)
    return {"task_id": task_id, "kind": "run_skill", "skill": skill_name}


ACTIONS = {
    "propose": _act_propose,
    "nudge": _act_nudge,
    "create_task": _act_create_task,
    "run_skill": _act_run_skill,
}


# ---------------------------------------------------------------------------
# Top-level evaluator — call once per brain cycle.
# ---------------------------------------------------------------------------

def _cooldown_active(automation):
    if not automation.cooldown_days or not automation.last_fired_at:
        return False
    last = automation.last_fired_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last) < timedelta(days=automation.cooldown_days)


def _normalize_cond_result(result):
    """Conditions can return bool (legacy) or {match, context} dict.
    Normalize to a (bool, dict) tuple. Missing context defaults to {}.
    """
    if isinstance(result, dict):
        return bool(result.get("match")), result.get("context") or {}
    return bool(result), {}


def _evaluate_conditions(automation):
    """Run all when[] entries. Returns (bool, merged_context).

    With when_logic == "all" every condition must match; for "any" one
    is enough. Context from matched conditions is merged (later wins)
    so actions can templatize over values the conditions extracted.
    """
    when = automation.when or []
    if not when:
        return False, {}
    logic = (automation.when_logic or "all").lower()
    results = []
    context = {}
    for trigger in when:
        kind = trigger.get("kind")
        handler = CONDITIONS.get(kind)
        if handler is None:
            logger.warning(
                f"[automation {automation.id}] unknown condition kind {kind!r}; treating as False"
            )
            results.append(False)
            continue
        try:
            matched, ctx = _normalize_cond_result(
                handler(automation, trigger.get("config") or {})
            )
        except Exception as e:
            logger.warning(
                f"[automation {automation.id}] condition {kind} error: {e}"
            )
            results.append(False)
            continue
        results.append(matched)
        if matched and ctx:
            context.update(ctx)
    ok = any(results) if logic == "any" else all(results)
    return ok, context


def _apply_context_to_config(config, context):
    """Walk a config dict and .format() any string fields against
    context. Leaves numbers / bools / lists of non-strings alone.
    Used so actions can reference {service}, {types}, etc. in their
    templated text without each action rewriting the substitution
    logic.
    """
    if not context:
        return config
    if isinstance(config, dict):
        return {k: _apply_context_to_config(v, context) for k, v in config.items()}
    if isinstance(config, list):
        return [_apply_context_to_config(v, context) for v in config]
    if isinstance(config, str):
        return _safe_format(config, context)
    return config


def _run_actions(automation, context=None):
    results = []
    for action in (automation.then or []):
        kind = action.get("kind")
        handler = ACTIONS.get(kind)
        if handler is None:
            logger.warning(
                f"[automation {automation.id}] unknown action kind {kind!r}; skipping"
            )
            results.append({"skipped": f"unknown kind {kind!r}"})
            continue
        try:
            config = _apply_context_to_config(action.get("config") or {}, context)
            results.append(handler(automation, config))
        except Exception as e:
            logger.warning(
                f"[automation {automation.id}] action {kind} error: {e}"
            )
            results.append({"error": str(e), "kind": kind})
    return results


def evaluate():
    """Run every active Automation and fire actions when conditions hold.

    Safe to call on every brain cycle — cooldowns gate re-firing even
    when the underlying condition stays true, and actions dedupe via
    the automation_id tag on emitted pupdates.

    Returns:
        dict with per-outcome counts + a details list suitable for
        cycle logging.
    """
    automations = (
        Automation.query
        .filter(Automation.status == "active")
        .order_by(Automation.id.asc())
        .all()
    )

    fired = 0
    cooldown = 0
    unmet = 0
    details = []

    for a in automations:
        if _cooldown_active(a):
            cooldown += 1
            details.append({"id": a.id, "outcome": "cooldown"})
            continue
        try:
            matched, context = _evaluate_conditions(a)
            if not matched:
                unmet += 1
                details.append({"id": a.id, "outcome": "unmet"})
                continue
        except Exception as e:
            logger.warning(f"[automation {a.id}] evaluation error: {e}")
            details.append({"id": a.id, "outcome": "error", "error": str(e)})
            continue

        actions_result = _run_actions(a, context=context)
        a.last_fired_at = datetime.now(timezone.utc)
        a.fire_count = (a.fire_count or 0) + 1
        fired += 1
        details.append({
            "id": a.id,
            "outcome": "fired",
            "actions": actions_result,
            "context": context,
        })

    if fired:
        db.session.commit()
        logger.info(f"[automations] fired {fired} (cooldown={cooldown} unmet={unmet})")

    return {"fired": fired, "cooldown": cooldown, "unmet": unmet, "details": details}


# ---------------------------------------------------------------------------
# Seeding + migration from AgentGoal
# ---------------------------------------------------------------------------

_CHAIN_SEEDS = [
    {
        "slug": "ci_fail_rollback_error_spike",
        "types": ["pr_ci_failed", "deploy_rollback", "error_spike"],
        "name": "Investigate CI fail → rollback → error spike incident",
        "description": (
            "When a repo trips CI failure, a rollback, and an error spike "
            "within 30 minutes, propose an investigation. Classic incident "
            "shape — spawning an Investigator gets the timeline assembled "
            "before the context goes stale."
        ),
    },
    {
        "slug": "deploy_blocked_stuck",
        "types": ["deploy_blocked", "deploy_stuck"],
        "name": "Investigate stuck deploy",
        "description": (
            "Deploy flagged as blocked AND stuck in the same 30-minute "
            "window usually means the release pipeline is wedged. "
            "Approving spawns an investigator to untangle it."
        ),
    },
    {
        "slug": "ci_fail_deploy_blocked",
        "types": ["pr_ci_failed", "deploy_blocked"],
        "name": "Investigate CI break blocking deploy",
        "description": (
            "CI red + deploy blocked on the same repo usually means the "
            "master build is broken. Investigate the failing check so a "
            "fix lands before the team is idle."
        ),
    },
    {
        "slug": "rollback_error_spike",
        "types": ["deploy_rollback", "error_spike"],
        "name": "Investigate rollback with error spike",
        "description": (
            "Rolled-back deploy + ongoing error spike suggests the rollback "
            "didn't restore healthy state. Propose a targeted investigation."
        ),
    },
    {
        "slug": "batch_job_error_spike",
        "types": ["batch_job_failing", "error_spike"],
        "name": "Investigate failing batch job + error spike",
        "description": (
            "Batch job failure correlated with an error spike often points "
            "to the same upstream. Propose an investigation to find the link."
        ),
    },
    {
        "slug": "rollback_batch_fail",
        "types": ["deploy_rollback", "batch_job_failing"],
        "name": "Investigate rollback breaking batch jobs",
        "description": (
            "A rollback that leaves batch jobs failing means the previous "
            "version has drift. Investigate what assumptions broke."
        ),
    },
]


def ensure_seed_chain_automations():
    """Seed one Automation per incident chain (replacing correlator
    CAUSE_CHAINS). Idempotent — matches on a marker in automation.extra
    so reseeding doesn't duplicate.
    """
    from sqlalchemy import func  # noqa: F401

    # Use the name as an identity shim; if the user renamed the row
    # we leave their version alone.
    created = 0
    for seed in _CHAIN_SEEDS:
        marker = seed["slug"]
        existing = (
            Automation.query
            .filter(Automation.created_by == "seed")
            .filter(Automation.name == seed["name"])
            .first()
        )
        if existing is not None:
            continue
        a = Automation(
            name=seed["name"],
            description=seed["description"],
            when=[{
                "kind": "pupdate_chain",
                "config": {
                    "types": seed["types"],
                    "within_minutes": 30,
                    "group_by": "repo",
                },
            }],
            when_logic="all",
            within_minutes=30,
            then=[{
                "kind": "propose",
                "config": {
                    "draft": {
                        "title": "Investigate incident on {service}",
                        "type": "investigation",
                        "priority": "high",
                        "repo": "{service}",
                        "description": (
                            "Correlated signals on {service}: {types}. "
                            "Approving spawns an investigator that walks the "
                            "worktree, assembles a timeline, and files a report."
                        ),
                    },
                },
            }],
            status="active",
            created_by="seed",
            scope_repo=None,
            cooldown_days=1,  # short cooldown — incidents re-fire quickly
        )
        db.session.add(a)
        created += 1

    if created:
        db.session.commit()
        logger.info(f"[automations] seeded {created} incident chain automation(s)")
    return created


def ensure_seed_automations():
    """For every configured repo, make sure the canonical 'keep overview
    current' automation exists. Idempotent.

    This replaces ensure_seed_goals() and keeps the one-watch-per-repo
    invariant the cartographer depends on.
    """
    from planet_maiko.config import load_config

    config = load_config()
    repos = (config.get("github") or {}).get("repos") or []
    if not repos:
        return 0

    cart_cfg = (
        ((config.get("brain") or {}).get("role_autonomy") or {}).get("cartographer") or {}
    )
    stale_days = int(cart_cfg.get("stale_days", 30))
    cooldown_days = int(cart_cfg.get("cooldown_days", 7))

    created = 0
    for repo in repos:
        # Already seeded? Match on (scope_repo + condition kind) so a
        # user who renamed the row doesn't get a duplicate.
        existing = (
            Automation.query
            .filter(Automation.scope_repo == repo)
            .filter(Automation.created_by == "seed")
            .all()
        )
        has_overview_watch = any(
            any(t.get("kind") == "overview_stale" for t in (a.when or []))
            for a in existing
        )
        if has_overview_watch:
            continue

        a = Automation(
            name=f"Keep {repo}'s overview current",
            description=(
                f"Atlas re-cartographs {repo} when its Repo Overview insight "
                f"is missing or older than {stale_days} days. Approving the "
                f"proposal spawns a one-shot cartographer run."
            ),
            when=[{
                "kind": "overview_stale",
                "config": {"repo": repo, "stale_days": stale_days},
            }],
            when_logic="all",
            then=[{
                "kind": "propose",
                "config": {
                    "draft": {
                        "title": f"Cartograph {repo}",
                        "type": "cartograph",
                        "priority": "normal",
                        "repo": repo,
                        "description": (
                            f"{repo} hasn't been cartographed in a while. "
                            f"Approving spawns Atlas to walk the tree and "
                            f"produce a fresh Repo Overview."
                        ),
                    },
                },
            }],
            status="active",
            created_by="seed",
            scope_repo=repo,
            cooldown_days=cooldown_days,
        )
        db.session.add(a)
        created += 1

    if created:
        db.session.commit()
        logger.info(f"[automations] seeded {created} overview watch(es)")
    return created


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
                "kind": "run_skill",
                "config": {"skill_name": s.id},
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


def migrate_agent_goals():
    """One-time import of existing AgentGoal rows into Automations.

    Runs on every startup (cheap no-op when the goals table is empty or
    gone); writes one Automation per goal. After a successful import,
    drops the goal row so subsequent boots don't re-import.

    Safe to delete this function after the goals table has been empty
    for at least one release cycle.
    """
    try:
        from planet_maiko.models.agent_goal import AgentGoal
    except Exception:
        return 0  # model removed; nothing to migrate

    goals = AgentGoal.query.all()
    if not goals:
        return 0

    migrated = 0
    for g in goals:
        when = []
        then = []
        if g.kind == "keep_overview_current":
            cfg = g.trigger_config or {}
            when = [{
                "kind": "overview_stale",
                "config": {
                    "repo": g.scope_repo,
                    "stale_days": int(cfg.get("stale_days", 30)),
                },
            }]
            then = [{
                "kind": "propose",
                "config": {
                    "draft": {
                        "title": f"Cartograph {g.scope_repo}",
                        "type": "cartograph",
                        "priority": "normal",
                        "repo": g.scope_repo,
                        "description": f"Refresh Atlas's overview of {g.scope_repo}.",
                    },
                },
            }]
            name = f"Keep {g.scope_repo}'s overview current"
            description = (g.extra or {}).get("description") or ""
        elif g.kind == "train_lora_when_ready":
            cfg = g.trigger_config or {}
            when = [{
                "kind": "lora_missing",
                "config": {
                    "repo": g.scope_repo,
                    "min_learnings": int(cfg.get("min_learnings", 10)),
                },
            }]
            then = [{
                "kind": "nudge",
                "config": {
                    "title": f"Ready to train a LoRA for {g.scope_repo}?",
                    "body": (
                        f"{g.scope_repo} has crossed the learning threshold "
                        f"and doesn't have a trained adapter yet."
                    ),
                    "url": "/knowledge?tab=training",
                    "action_hint": "Open Training",
                },
            }]
            name = f"Nudge when {g.scope_repo} is ready to train"
            description = (g.extra or {}).get("description") or ""
        else:
            # Unknown kind — write a minimal placeholder that the user
            # can inspect and edit. No detector for the condition kind
            # will exist, so it'll simply never fire; not harmful.
            name = f"Imported goal: {g.kind}"
            description = f"Auto-migrated from legacy AgentGoal (kind={g.kind})."
            when = []
            then = []

        automation = Automation(
            name=name,
            description=description,
            when=when,
            when_logic="all",
            then=then,
            status=g.status,
            last_fired_at=g.last_fired_at,
            fire_count=0,
            created_by="seed" if g.created_by == "seed" else "proposal" if g.created_by == "proposal" else "user",
            agent_profile_id=g.agent_profile_id,
            scope_repo=g.scope_repo,
            cooldown_days=7,
        )
        db.session.add(automation)
        db.session.delete(g)
        migrated += 1

    if migrated:
        db.session.commit()
        logger.info(f"[automations] migrated {migrated} AgentGoal row(s) to Automation")
    return migrated
