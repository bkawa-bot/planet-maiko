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

# Submodule imports — engine pulls CONDITIONS / ACTIONS dispatch tables
# and the shared format helper from sibling files.
from .helpers import _safe_format
from .conditions import CONDITIONS
from .actions import (
    resolve_action,
    _interpolate,
    _pupdate_snapshot,
    format_pupdate_for_context,
)

logger = logging.getLogger(__name__)


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
    """Conditions can return bool or {match, context} dict.
    Normalize to a (bool, dict) tuple. Missing context defaults to {}.
    """
    if isinstance(result, dict):
        return bool(result.get("match")), result.get("context") or {}
    return bool(result), {}


def _evaluate_conditions(automation, pupdate=None):
    """Run all when[] entries. Returns (bool, merged_context, outcomes).

    When `pupdate` is supplied (pupdate-scope evaluation), each
    condition handler gets it. Handlers that don't care ignore the
    kwarg; pupdate_match uses it to evaluate against that specific
    pupdate instead of scanning recent ones.

    with_logic == "all" = every condition must match; "any" = one is
    enough. Context from matched conditions is merged (later wins)
    so actions can templatize over the extracted values.

    The third return value `outcomes` is a list of per-condition dicts
    `[{kind, matched, reason?}]` — exposed so the cycle logger can
    surface "automation X was unmet because pupdate_match=false,
    repo_in=true" instead of opaque counters.
    """
    when = automation.when or []
    outcomes = []
    if not when:
        return False, {}, outcomes
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
            outcomes.append({"kind": kind, "matched": False, "reason": "unknown kind"})
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
            outcomes.append({"kind": kind, "matched": False, "reason": f"error: {e}"})
            results.append(False)
            continue
        outcomes.append({"kind": kind, "matched": matched})
        results.append(matched)
        if matched and ctx:
            context.update(ctx)
    ok = any(results) if logic == "any" else all(results)
    return ok, context, outcomes


def _format_outcomes(outcomes):
    """Pretty-format a list of condition outcomes for log output.
    `pupdate_match=✓ repo_in=✗(error: …)` style, compact + scannable.
    """
    parts = []
    for o in outcomes:
        mark = "✓" if o["matched"] else "✗"
        suffix = ""
        if not o["matched"] and o.get("reason"):
            suffix = f"({o['reason']})"
        parts.append(f"{o['kind']}={mark}{suffix}")
    return " ".join(parts) if parts else "(no conditions)"


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
    # Cycle-scope automations match a pupdate via condition matching
    # but the engine doesn't pass the pupdate object through —
    # _cond_pupdate_match leaves the id in context.pupdate_id and
    # moves on. Fetch the actual row here so action handlers (notify_me,
    # spawn_agent_job_from_pupdate, etc.) get a real pupdate to attach
    # source_pupdate_id from. Without this, notification memos came
    # out with source_pupdate_id=None and the automation re-fired on
    # every cycle creating duplicate memos.
    if pupdate is None and context and context.get("pupdate_id"):
        try:
            pupdate = db.session.get(Pupdate, context["pupdate_id"])
        except Exception:
            pupdate = None
    results = []
    for action in (automation.then or []):
        kind = action.get("kind")
        handler = resolve_action(kind)
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
        name = a.name or f"<id={a.id}>"
        if _cooldown_active(a):
            cooldown += 1
            details.append({"id": a.id, "outcome": "cooldown"})
            # Log when each cooldown will lift so the user can debug
            # "why didn't this fire" without doing the math themselves.
            if a.last_fired_at:
                ago = datetime.now(timezone.utc) - a.last_fired_at.replace(
                    tzinfo=timezone.utc
                ) if a.last_fired_at.tzinfo is None else (
                    datetime.now(timezone.utc) - a.last_fired_at
                )
                logger.info(
                    f"[automation '{name}' id={a.id}] cooldown — fired "
                    f"{int(ago.total_seconds() / 60)}m ago "
                    f"(cooldown_days={a.cooldown_days})"
                )
            continue
        try:
            matched, context, outcomes = _evaluate_conditions(a)
            if not matched:
                unmet += 1
                details.append({
                    "id": a.id, "outcome": "unmet",
                    "outcomes": outcomes,
                })
                logger.info(
                    f"[automation '{name}' id={a.id}] unmet — "
                    f"{_format_outcomes(outcomes)}"
                )
                continue
        except Exception as e:
            logger.warning(f"[automation '{name}' id={a.id}] evaluation error: {e}")
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
        logger.info(
            f"[automation '{name}' id={a.id}] fired — "
            f"{_format_outcomes(outcomes)} → "
            f"{len(actions_result)} action(s)"
        )

    # Pupdate-scope: iterate each unprocessed pupdate, first matching
    # automation (ordered by id) claims it. One rule fires per pupdate,
    # and the pupdate is marked brain_processed regardless (matched or
    # not; the processor's focus gating + pr_review_commented path
    # still runs in its own phase, but the rule dispatch happens here).
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
            # Per-pupdate diagnostic trail — which automations were
            # tried, which conditions failed. Logged at DEBUG so the
            # default INFO log doesn't drown in pupdate × rule misses,
            # but flips on cleanly when the user is hunting a specific
            # "why didn't my automation match this pupdate" question.
            tried = []
            for a in pupdate_automations:
                name = a.name or f"<id={a.id}>"
                try:
                    matched, context, outcomes = _evaluate_conditions(a, pupdate=p)
                except Exception as e:
                    logger.warning(f"[automation '{name}' id={a.id}] pupdate-scope eval error: {e}")
                    tried.append(f"'{name}'=error")
                    continue
                if not matched:
                    tried.append(f"'{name}'=miss[{_format_outcomes(outcomes)}]")
                    continue
                _run_actions(a, context=context, pupdate=p)
                a.last_fired_at = datetime.now(timezone.utc)
                a.fire_count = (a.fire_count or 0) + 1
                pupdate_fired += 1
                fired_for_this = True
                logger.info(
                    f"[automation '{name}' id={a.id}] fired on pupdate "
                    f"{p.id} (type={p.type})"
                )
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
                # Surface unmatched pupdates at INFO so the user can
                # see "PR webhook landed but no automation claimed it"
                # without enabling DEBUG. The per-rule miss reasons
                # stay at DEBUG to avoid drowning the log in noise.
                logger.info(
                    f"[automation pupdate-scope] no rule matched "
                    f"pupdate {p.id} (type={p.type}, source={p.source})"
                )
                if tried:
                    logger.debug(
                        f"[automation pupdate-scope] tried for {p.id}: "
                        + " ".join(tried)
                    )
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


# Seeding lives in seeding.py; re-export so callers can do
# `from planet_maiko.brain.automations import ensure_seed_automations`.
from .seeding import (  # noqa: E402,F401
    _RULE_SEEDS,
    ensure_seed_rule_automations,
    ensure_seed_automations,
    ensure_plugin_default_automations,
    migrate_obsolete_create_task_seeds,
)
