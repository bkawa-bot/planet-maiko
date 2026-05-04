"""Condition evaluators for the Automation engine.

Each `_cond_*` function returns either a bool, or a
`{"match": bool, "context": dict}` dict so the matcher can carry
forward repo/service identifiers into the action's templating.

Raised exceptions are caught at the engine level — detectors
shouldn't raise for "no match", they should return False.

The CONDITIONS dict at the bottom is the dispatch table the engine
walks; new condition kinds register here.
"""

import logging
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.automation import Automation
from planet_maiko.models.pupdate import Pupdate

logger = logging.getLogger(__name__)




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
    "pupdate_match": _cond_pupdate_match,
    "pupdate_chain": _cond_pupdate_chain,
}