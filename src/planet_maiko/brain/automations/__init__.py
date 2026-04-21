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

def _cond_cadence(automation, config, pupdate=None):
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


def _cond_overview_stale(automation, config, pupdate=None):
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


def _cond_lora_missing(automation, config, pupdate=None):
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


def _pupdate_matches_criteria(pupdate, config):
    """Evaluate rule-style criteria against a single pupdate. Reused
    by both the cycle-scope variant (scans recent) and the
    pupdate-scope variant (tests one-at-a-time)."""
    if "source" in config and pupdate.source != config["source"]:
        return False
    if "type" in config and pupdate.type != config["type"]:
        return False
    if "types" in config and pupdate.type not in config["types"]:
        return False
    if "type_prefix" in config and not pupdate.type.startswith(config["type_prefix"]):
        return False
    if "priority" in config and pupdate.priority != config["priority"]:
        return False
    if "priority_in" in config and pupdate.priority not in config["priority_in"]:
        return False
    if "actionable" in config and bool(pupdate.actionable) != bool(config["actionable"]):
        return False
    if "has_tag" in config and config["has_tag"] not in (pupdate.tags or []):
        return False
    if "title_contains" in config:
        needle = (config["title_contains"] or "").lower()
        if needle and needle not in (pupdate.title or "").lower():
            return False
    return True


def _cond_pupdate_match(automation, config, pupdate=None):
    """Dual-mode pupdate matcher.

    - Cycle scope (no pupdate arg): scans recent non-dismissed pupdates
      within `within_minutes` (default 60) and matches if any fit the
      criteria. Context includes the first match's fields so actions
      can templatize "{service}" etc.
    - Pupdate scope (pupdate arg supplied by the engine's per-pupdate
      loop): evaluates criteria against that specific pupdate only.
      Context carries pupdate metadata through to the action.

    Config supports the full rule-shape criteria set:
    source / type / types / type_prefix / priority / priority_in /
    actionable / has_tag / title_contains (+ within_minutes in cycle mode).
    """
    if pupdate is not None:
        if not _pupdate_matches_criteria(pupdate, config):
            return {"match": False}
        repo = (pupdate.extra or {}).get("repo")
        if not repo and (pupdate.tags or []):
            repo = (pupdate.tags or [None])[0]
        return {
            "match": True,
            "context": {
                "service": repo or "",
                "pupdate_id": pupdate.id,
                "pupdate_type": pupdate.type,
                "title": pupdate.title or "",
            },
        }

    # Cycle-scope path
    within = int(config.get("within_minutes", 60))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within)
    candidates = (
        Pupdate.query
        .filter(Pupdate.timestamp >= cutoff, Pupdate.dismissed == False)  # noqa: E712
        .order_by(Pupdate.timestamp.desc())
        .limit(100)
        .all()
    )
    for p in candidates:
        if _pupdate_matches_criteria(p, config):
            repo = (p.extra or {}).get("repo")
            if not repo and (p.tags or []):
                repo = (p.tags or [None])[0]
            return {
                "match": True,
                "context": {
                    "service": repo or "",
                    "pupdate_id": p.id,
                    "pupdate_type": p.type,
                    "title": p.title or "",
                    "pupdate_ids": [p.id],
                },
            }
    return {"match": False}


def _cond_pupdate_chain(automation, config, pupdate=None):
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

def _act_run_agent_job(automation, config, pupdate=None):
    """Spawn an AgentJob — the pack-owned "run this skill / role" primitive.

    Config:
      kind: str           — job kind (cartograph, investigation,
                            repo_analysis, or a skill name). Maps to
                            task.type in the execute phase — determines
                            which role spawns.
      title: str          — defaults to automation.name.
      description: str    — skill input / body / instructions for the agent.
      scope_repo: str     — org/repo; drives worktree resolution.
      priority: str       — low | normal | high | urgent. Default normal.
      ask_first: bool     — true → status=pending_approval (user approves
                            from the AgentJobs dashboard). false → direct
                            queue for the next execute-phase tick.
    """
    from planet_maiko.models.agent_job import AgentJob

    ask_first = bool(config.get("ask_first", False))
    kind = config.get("kind") or "todo"
    title = config.get("title") or automation.name
    repo = config.get("scope_repo") or automation.scope_repo or None
    description = config.get("description") or automation.description or ""
    priority = config.get("priority") or "normal"

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
        requires_approval=ask_first,
        status="pending_approval" if ask_first else "queued",
        approved_by=None if ask_first else "auto",
        approved_at=None if ask_first else datetime.now(timezone.utc),
        extra={"from_automation": automation.id},
    )
    db.session.add(job)
    return {"job_id": job_id, "kind": "run_agent_job", "status": job.status}


def _act_create_task(automation, config, pupdate=None):
    """Create a user-owed Task.

    Use this when the automation surfaces work the *user* needs to do
    (a todo, a bug, something they personally own). For pack-owned one-
    shot runs (cartograph, investigate, skills), use `run_agent_job`.

    Config:
      type: str           — todo | bug | feature | coding etc.
      title: str
      description: str
      priority: str
      repo: str
    """
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
            "description": config.get("description") or automation.description or "",
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


def _act_spawn_agent_job_from_pupdate(automation, config, pupdate=None):
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

    job_id = f"job-{uuid.uuid4().hex[:10]}"
    job = AgentJob(
        id=job_id,
        kind=kind,
        title=title,
        description=description,
        scope_repo=repo,
        priority=config.get("priority") or pupdate.priority or "normal",
        created_by="automation",
        automation_id=automation.id,
        requires_approval=ask_first,
        status="pending_approval" if ask_first else "queued",
        approved_by=None if ask_first else "auto",
        approved_at=None if ask_first else datetime.now(timezone.utc),
        extra={
            "from_automation": automation.id,
            "triggered_by_pupdate": pupdate.id,
        },
    )
    db.session.add(job)
    return {"job_id": job_id, "kind": "spawn_agent_job_from_pupdate", "status": job.status}


def _act_dismiss_pupdate(automation, config, pupdate=None):
    if pupdate is None:
        return {"skipped": "dismiss_pupdate requires pupdate context"}
    pupdate.dismissed = True
    pupdate.dismissed_at = datetime.now(timezone.utc)
    return {"kind": "dismiss_pupdate", "pupdate_id": pupdate.id}


def _act_create_task_from_pupdate(automation, config, pupdate=None):
    """Rule-style create-a-task: use the pupdate's title/priority as the
    task seed, letting config override task_type and task_priority.
    Mirrors _execute_create_task in the old processor."""
    if pupdate is None:
        return {"skipped": "create_task_from_pupdate requires pupdate context"}
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route, is_ready
    import uuid as _uuid

    task_type = config.get("task_type") or pupdate.type
    task_priority = config.get("task_priority") or pupdate.priority or "normal"
    task_id = f"task-{_uuid.uuid4().hex[:10]}"
    repo = (pupdate.extra or {}).get("repo") or ""
    task = Task(
        id=task_id,
        title=pupdate.title,
        type=task_type,
        priority=task_priority,
        status="new",
        source_pupdate_id=pupdate.id,
        url=pupdate.url,
        tags=list(pupdate.tags or []),
        extra={
            "description": pupdate.body or "",
            "repo": repo,
            "from_automation": automation.id,
        },
    )
    db.session.add(task)
    db.session.flush()
    route(task)
    if not is_ready(task):
        task.status = "blocked"
    return {"kind": "create_task_from_pupdate", "task_id": task_id, "pupdate_id": pupdate.id}


def _act_complete_linked_task(automation, config, pupdate=None):
    """Close review / coding tasks whose url matches this pupdate's url.
    Replaces the old ACTION_COMPLETE_TASK in rules.py — same cleanup
    semantics, now living inside the Automation engine."""
    if pupdate is None or not pupdate.url:
        return {"skipped": "no url"}
    from planet_maiko.models.task import Task

    closed_review = 0
    closed_coding = 0
    review_tasks = Task.query.filter(
        Task.url == pupdate.url,
        Task.type.in_(["review", "pr_review"]),
        Task.status.in_(["new", "in_progress"]),
    ).all()
    for t in review_tasks:
        t.status = "done"
        t.updated_at = datetime.now(timezone.utc)
        closed_review += 1

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

    return {
        "kind": "complete_linked_task",
        "review_tasks_closed": closed_review,
        "coding_tasks_closed": closed_coding,
    }


def _act_skip(automation, config, pupdate=None):
    """Explicit no-op — useful for 'mark this pattern as handled,
    don't dispatch anything.' Pairs with pupdate-scope automations
    where you want the first-match behavior to claim the pupdate but
    not produce any side effects."""
    return {"kind": "skip"}


ACTIONS = {
    # Cycle-scope
    "run_agent_job": _act_run_agent_job,
    "create_task": _act_create_task,
    # Pupdate-scope (require context.pupdate to operate).
    "spawn_agent_job_from_pupdate": _act_spawn_agent_job_from_pupdate,
    "dismiss_pupdate": _act_dismiss_pupdate,
    "create_task_from_pupdate": _act_create_task_from_pupdate,
    "complete_linked_task": _act_complete_linked_task,
    "skip": _act_skip,
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


def _evaluate_conditions(automation, pupdate=None):
    """Run all when[] entries. Returns (bool, merged_context).

    When `pupdate` is supplied (pupdate-scope evaluation), each
    condition handler gets it — handlers that don't care ignore the
    kwarg; pupdate_match uses it to evaluate against that specific
    pupdate instead of scanning recent ones.

    with_logic == "all" = every condition must match; "any" = one is
    enough. Context from matched conditions is merged (later wins)
    so actions can templatize over the extracted values.
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
                handler(automation, trigger.get("config") or {}, pupdate=pupdate)
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


def _run_actions(automation, context=None, pupdate=None):
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
            results.append(handler(automation, config, pupdate=pupdate))
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
    # Cycle-scope: evaluate once per tick
    cycle_automations = (
        Automation.query
        .filter(Automation.status == "active")
        .filter(Automation.execution_scope == "cycle")
        .order_by(Automation.id.asc())
        .all()
    )

    fired = 0
    cooldown = 0
    unmet = 0
    details = []

    for a in cycle_automations:
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

    # Pupdate-scope: iterate each unprocessed pupdate, first matching
    # automation (ordered by id) claims it. Mirrors the old rules.py
    # evaluate() semantic: one rule fires per pupdate, and the pupdate
    # is marked brain_processed regardless (matched or not — the
    # processor's focus gating + pr_review_commented path still runs
    # in its own phase, but the rule dispatch happens here).
    pupdate_automations = (
        Automation.query
        .filter(Automation.status == "active")
        .filter(Automation.execution_scope == "pupdate")
        .order_by(Automation.id.asc())
        .all()
    )
    pupdate_fired = 0
    pupdate_unmatched = 0
    if pupdate_automations:
        unprocessed = (
            Pupdate.query
            .filter(Pupdate.brain_processed == False)  # noqa: E712
            .filter(Pupdate.dismissed == False)  # noqa: E712
            .order_by(Pupdate.timestamp.asc())
            .limit(200)
            .all()
        )
        for p in unprocessed:
            # pr_review_commented stays in the processor — it requires
            # agent-wake logic that doesn't fit an action dispatch.
            if p.type == "pr_review_commented":
                continue
            fired_for_this = False
            for a in pupdate_automations:
                try:
                    matched, context = _evaluate_conditions(a, pupdate=p)
                except Exception as e:
                    logger.warning(f"[automation {a.id}] pupdate-scope eval error: {e}")
                    continue
                if not matched:
                    continue
                _run_actions(a, context=context, pupdate=p)
                a.last_fired_at = datetime.now(timezone.utc)
                a.fire_count = (a.fire_count or 0) + 1
                pupdate_fired += 1
                fired_for_this = True
                break  # first-match wins
            if not fired_for_this:
                pupdate_unmatched += 1
            p.brain_processed = True

    if fired or pupdate_fired:
        db.session.commit()
        logger.info(
            f"[automations] cycle={fired} pupdate={pupdate_fired} "
            f"(cooldown={cooldown} unmet={unmet} unmatched={pupdate_unmatched})"
        )

    return {
        "fired": fired,
        "cooldown": cooldown,
        "unmet": unmet,
        "pupdate_fired": pupdate_fired,
        "pupdate_unmatched": pupdate_unmatched,
        "details": details,
    }


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


_RULE_SEEDS = [
    {
        "name": "Auto-dismiss CI passing",
        "description": "CI-passed notifications are pure noise — don't need to see them.",
        "match": {"type": "pr_ci_passed"},
        "action": "dismiss_pupdate",
        "action_config": {},
    },
    {
        "name": "Auto-dismiss bot PRs",
        "description": "Dependabot / renovate PRs don't need human attention.",
        "match": {"type_prefix": "pr_", "title_contains": "dependabot"},
        "action": "dismiss_pupdate",
        "action_config": {},
    },
    {
        "name": "Create task on PR review request",
        "description": "When a teammate requests your review, create a high-priority review task.",
        "match": {"type": "pr_review_requested"},
        "action": "create_task_from_pupdate",
        "action_config": {"task_type": "review", "task_priority": "high"},
    },
    {
        "name": "Create task on Linear assignment",
        "description": "A Linear issue assigned to you becomes a todo task.",
        "match": {"type": "linear_assigned"},
        "action": "create_task_from_pupdate",
        "action_config": {"task_type": "todo"},
    },
    {
        "name": "Create task on PR changes requested",
        "description": "Reviewer wants changes — create a high-priority bug task to address them.",
        "match": {"type": "pr_changes_requested"},
        "action": "create_task_from_pupdate",
        "action_config": {"task_type": "bug", "task_priority": "high"},
    },
    {
        "name": "Create task on CI failure",
        "description": "CI red on your PR — create a high-priority bug task so it doesn't get forgotten.",
        "match": {"type": "pr_ci_failed"},
        "action": "create_task_from_pupdate",
        "action_config": {"task_type": "bug", "task_priority": "high"},
    },
    {
        "name": "Close linked task on PR approved",
        "description": "An approval means the review's done — close any review/coding task pointing at this PR.",
        "match": {"type": "pr_approved"},
        "action": "complete_linked_task",
        "action_config": {},
    },
    {
        "name": "Close linked task on PR merged",
        "description": "PR merged — close linked tasks and clean up any worktree backing them.",
        "match": {"type": "pr_merged"},
        "action": "complete_linked_task",
        "action_config": {},
    },
]


def ensure_seed_rule_automations():
    """Seed pupdate-scope Automations for the eight canonical matchers
    that used to live in rules.py. Idempotent on (name, execution_scope).
    """
    created = 0
    for seed in _RULE_SEEDS:
        existing = (
            Automation.query
            .filter(Automation.name == seed["name"])
            .filter(Automation.execution_scope == "pupdate")
            .first()
        )
        if existing is not None:
            continue
        a = Automation(
            name=seed["name"],
            description=seed["description"],
            when=[{"kind": "pupdate_match", "config": seed["match"]}],
            when_logic="all",
            then=[{"kind": seed["action"], "config": seed["action_config"]}],
            status="active",
            created_by="seed",
            execution_scope="pupdate",
            cooldown_days=0,
        )
        db.session.add(a)
        created += 1
    if created:
        db.session.commit()
        logger.info(f"[automations] seeded {created} pupdate rule automation(s)")
    return created


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
                "kind": "run_agent_job",
                "config": {
                    "ask_first": True,
                    "kind": "investigation",
                    "title": "Investigate incident on {service}",
                    "priority": "high",
                    "scope_repo": "{service}",
                    "description": (
                        "Correlated signals on {service}: {types}. "
                        "Approving spawns an investigator that walks the "
                        "worktree, assembles a timeline, and files a report."
                    ),
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
                "kind": "run_agent_job",
                "config": {
                    "ask_first": True,
                    "kind": "cartograph",
                    "title": f"Cartograph {repo}",
                    "priority": "normal",
                    "scope_repo": repo,
                    "description": (
                        f"{repo} hasn't been cartographed in a while. "
                        f"Approving spawns Atlas to walk the tree and "
                        f"produce a fresh Repo Overview."
                    ),
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
    "brainstorm", "morning-brief", "evening-wrap", "checkin",
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
