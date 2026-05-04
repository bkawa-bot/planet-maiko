"""Automation engine — evaluates every active Automation row each brain
cycle and fires the action chain when conditions hold.

Condition-based proposals, user-editable, surfaced in the Automations
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
    """Fires when a repo's cartographer overview is missing or older
    than `stale_days`.

    Repo selection:
      - `repo: "org/name"` — check that repo specifically.
      - `repo: ""` / `"*"` / omitted — wildcard. Walks every repo in
        `config.github.repos` and fires on the first stale one; context
        carries `{repo: <matched>}` so the spawned action's `scope_repo`
        fallback lands on the right worktree.

    Wildcard mode returns one match per cycle (the first stale repo
    found). Subsequent cycles pick up the next stale repo until the
    backlog drains, so the Automations page stays a single row instead
    of one-per-repo.
    """
    stale_days = int(config.get("stale_days", 30))
    explicit = config.get("repo") or automation.scope_repo
    wildcard = not explicit or explicit == "*"

    if wildcard:
        repos = _configured_repos()
    else:
        repos = [explicit]

    for repo in repos:
        if _repo_overview_is_stale(repo, stale_days):
            return {"match": True, "context": {"repo": repo}}
    return {"match": False}


def _repo_overview_is_stale(repo, stale_days):
    from planet_maiko.models.insight import Insight
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
    """Fires when a repo has enough active learnings to train on but no
    agent profile for that scope has an adapter_path set. Same wildcard
    semantics as overview_stale — empty/`"*"` repo iterates all
    configured repos, returns the first match's repo in context.
    """
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.agent_profile import AgentProfile

    min_learnings = int(config.get("min_learnings", 10))
    explicit = config.get("repo") or automation.scope_repo
    wildcard = not explicit or explicit == "*"

    repos = _configured_repos() if wildcard else [explicit]

    for repo in repos:
        active_count = (
            Learning.query
            .filter(Learning.status == "active")
            .filter(Learning.scope_repo == repo)
            .count()
        )
        if active_count < min_learnings:
            continue
        has_adapter = any(
            (p.extra or {}).get("adapter_path")
            for p in AgentProfile.query.filter(AgentProfile.scope_repo == repo).all()
        )
        if has_adapter:
            continue
        return {"match": True, "context": {"repo": repo}}
    return {"match": False}


def _configured_repos():
    """Return the list of `org/repo` strings from config.github.repos,
    or [] if none configured. Used by wildcard conditions that need to
    iterate every repo Maiko tracks.
    """
    try:
        from planet_maiko.config import load_config
        return (load_config().get("github") or {}).get("repos") or []
    except Exception:
        return []


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
            # Pass context so handlers can pull chain-level info (service,
            # pupdate_ids) even when the string templates didn't surface it
            # — e.g., auto-filling scope_repo from a chain's shared repo.
            results.append(handler(automation, config, pupdate=pupdate, context=context))
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
                # Auto-dismiss: the automation routed this pupdate to
                # its downstream effect (task, memo, agent job). The
                # pupdate is a queue event — once routed, it has no
                # remaining value sitting in the inbox or the overview's
                # context. Individual actions can still mark
                # brain_processed early if they need to suppress
                # re-firing between the action and this commit, but the
                # dismiss happens here uniformly.
                if not p.dismissed:
                    p.dismissed = True
                    p.dismissed_at = datetime.now(timezone.utc)
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


# Seeding + migrations were extracted to keep this module focused on
# the engine. Import here so existing call sites — `from
# planet_maiko.brain.automations import ensure_seed_automations` — keep
# resolving without churn.
from .seeding import (  # noqa: E402,F401
    _RULE_SEEDS,
    ensure_seed_rule_automations,
    ensure_seed_automations,
    ensure_plugin_default_automations,
)
from .migrations import (  # noqa: E402,F401
    _RETIRED_OPS_TYPES,
    migrate_archive_retired_chain_seeds,
    migrate_per_repo_overview_watches,
    migrate_scheduled_skills,
    migrate_legacy_action_kinds,
    migrate_tasks_to_agent_jobs,
)
