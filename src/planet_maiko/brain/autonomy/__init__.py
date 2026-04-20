"""Role-as-intent autonomy — self-triggered proposals from role-native agents.

Stage 0 (this module): cartographer-only. Atlas watches for repos whose
Repo Overview insight is missing or stale, and emits an agent_proposal
pupdate so the user can approve a refresh with one click. Approval
runs through the same `agent_proposal` → `approve_proposal` → routed
task flow that powers every other proposed task — no new execution
path, no new permission surface.

The detector is intentionally conservative:
  - Only fires for repos in `config.github.repos` (already user-sanctioned).
  - Skips repos that already have a non-dismissed refresh proposal
    queued.
  - Skips repos whose prior refresh proposal was dismissed inside the
    cooldown window (stops the "user said no, Atlas keeps asking" loop).
  - Caps proposals per cycle so a fresh install with 10 repos doesn't
    flood the inbox on the first tick.

Later stages will generalize this module into a proper goals model;
for now it's a single hardcoded detector that proves the pattern.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.insight import Insight

logger = logging.getLogger(__name__)


# Each role_autonomy detector attaches a unique tag to the proposals
# it emits so we can find them again for dedup / cooldown without
# having to parse titles. Keep this stable — the dedup query above
# depends on it.
_TAG_PREFIX = "role_trigger"


def _cartograph_tag(repo):
    return f"{_TAG_PREFIX}:cartograph_refresh:{repo}"


def _latest_overview_for_repo(repo):
    """Most recent ACTIVE overview insight for the given repo, or None.

    We look at active (approved) insights only — a pending or dismissed
    overview doesn't count as "current coverage" even if it's the most
    recent row. Tag shape matches what /insights/cartograph produces:
    approved Insights are tagged ["overview", "cartographer"] and
    scoped to the repo.
    """
    rows = (
        Insight.query
        .filter(Insight.repo_scope == repo)
        .filter(Insight.status == "active")
        .order_by(Insight.last_confirmed_at.desc())
        .limit(20)
        .all()
    )
    for ins in rows:
        tags = ins.tags or []
        if "overview" in tags or "cartographer" in tags:
            return ins
    return None


def _pupdate_in_flight(tag):
    """True if a non-dismissed proposal with this tag already exists.

    We don't want to pile up three "refresh planet-maiko" proposals
    just because the user hasn't gotten to the first one yet.
    """
    return (
        Pupdate.query
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .filter(Pupdate.tags.contains(tag))
        .first()
    ) is not None


def _cooldown_active(tag, cooldown_days):
    """True if the most recent dismissed proposal with this tag was
    dismissed within the cooldown window.

    Purpose: when the user dismisses "refresh planet-maiko overview",
    we should wait before asking again. Without this, the next brain
    cycle would immediately re-propose the same thing, which is
    exactly the kind of nagging that makes autonomy feel bad.
    """
    if cooldown_days <= 0:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
    recent = (
        Pupdate.query
        .filter(Pupdate.dismissed == True)  # noqa: E712
        .filter(Pupdate.tags.contains(tag))
        .filter(Pupdate.dismissed_at.isnot(None))
        .order_by(Pupdate.dismissed_at.desc())
        .first()
    )
    if recent is None:
        return False
    dismissed_at = recent.dismissed_at
    if dismissed_at and dismissed_at.tzinfo is None:
        dismissed_at = dismissed_at.replace(tzinfo=timezone.utc)
    return dismissed_at and dismissed_at >= cutoff


def _emit_cartograph_proposal(repo, *, reason, overview_age_days=None):
    """Create an agent_proposal pupdate that approving turns into a
    cartograph task. Shape matches what CartographLauncher produces —
    same Task.type, same extra.repo — so approve_proposal + route +
    _phase_execute_agent_tasks handle it without any new branching.

    The reasoning field is the user-facing "why Atlas is proposing this"
    string; ProposalCard renders it in the card body.
    """
    tag = _cartograph_tag(repo)
    pupdate_id = f"role-trigger-cartograph-{repo.replace('/', '_')}-{uuid.uuid4().hex[:6]}"

    if reason == "missing":
        title = f"Cartograph {repo}? No overview on file."
        body = (
            f"{repo} has never been cartographed. Approving spawns Atlas "
            f"to walk the tree and draft a Repo Overview insight for it. "
            f"Overviews inject into every agent's CLAUDE.md so new pups "
            f"start with the lay of the land."
        )
    else:  # stale
        age_str = f"{overview_age_days}d old" if overview_age_days is not None else "stale"
        title = f"Refresh {repo}'s overview? It's {age_str}."
        body = (
            f"{repo}'s Repo Overview was last confirmed {age_str}. "
            f"Approving re-runs Atlas so the insight reflects today's "
            f"tree — agents starting work on this repo pick up the "
            f"refreshed map via CLAUDE.md."
        )

    pupdate = Pupdate(
        id=pupdate_id,
        source="maiko",
        source_id=f"role-trigger/cartograph_refresh/{repo}",
        type="agent_proposal",
        priority="low",
        title=title,
        body=body,
        actionable=True,
        action_hint="Approve / dismiss",
        tags=["proposal", "from_maiko", tag],
        extra={
            "from_agent_id": None,  # no specific author; this is Maiko herself
            "draft": {
                "title": f"Cartograph {repo}",
                "type": "cartograph",
                "priority": "normal",
                "repo": repo,
                "description": body,
            },
            "role_trigger": {
                "kind": "cartograph_refresh",
                "repo": repo,
                "reason": reason,
                "overview_age_days": overview_age_days,
            },
        },
        # Proposals skip LLM triage — they're already a human-ask, not
        # a noisy source signal. Setting brain_processed=True keeps the
        # pupdate processor from re-interpreting them.
        brain_processed=True,
    )
    db.session.add(pupdate)
    return pupdate


def evaluate():
    """Run every role_autonomy detector and emit proposals.

    Safe to call on every brain cycle — dedup + cooldown guard against
    double-proposing, and the per-cycle cap bounds proposal rate.

    Returns:
        dict with counts by outcome (proposed, skipped_in_flight,
        skipped_cooldown, skipped_fresh) and a short `details` list
        per-repo for log triage.
    """
    from planet_maiko.config import load_config

    config = load_config()
    cfg = ((config.get("brain") or {}).get("role_autonomy") or {}).get("cartographer") or {}
    if not cfg.get("enabled", False):
        return {"proposed": 0, "skipped": 0, "reason": "disabled"}

    stale_days = int(cfg.get("stale_days", 30))
    cooldown_days = int(cfg.get("cooldown_days", 7))
    max_per_cycle = int(cfg.get("max_proposals_per_cycle", 2))

    repos = (config.get("github") or {}).get("repos") or []
    if not repos:
        return {"proposed": 0, "skipped": 0, "reason": "no_repos"}

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=stale_days)

    proposed = 0
    skipped_in_flight = 0
    skipped_cooldown = 0
    skipped_fresh = 0
    details = []

    for repo in repos:
        if proposed >= max_per_cycle:
            break

        tag = _cartograph_tag(repo)
        if _pupdate_in_flight(tag):
            skipped_in_flight += 1
            details.append({"repo": repo, "outcome": "in_flight"})
            continue
        if _cooldown_active(tag, cooldown_days):
            skipped_cooldown += 1
            details.append({"repo": repo, "outcome": "cooldown"})
            continue

        overview = _latest_overview_for_repo(repo)
        if overview is None:
            _emit_cartograph_proposal(repo, reason="missing")
            proposed += 1
            details.append({"repo": repo, "outcome": "proposed_missing"})
            continue

        last_confirmed = overview.last_confirmed_at
        if last_confirmed and last_confirmed.tzinfo is None:
            last_confirmed = last_confirmed.replace(tzinfo=timezone.utc)
        if last_confirmed is None or last_confirmed < stale_cutoff:
            age_days = None
            if last_confirmed is not None:
                age_days = int((now - last_confirmed).total_seconds() // 86400)
            _emit_cartograph_proposal(
                repo, reason="stale", overview_age_days=age_days,
            )
            proposed += 1
            details.append({
                "repo": repo, "outcome": "proposed_stale",
                "age_days": age_days,
            })
            continue

        skipped_fresh += 1
        details.append({"repo": repo, "outcome": "fresh"})

    if proposed:
        db.session.commit()
        logger.info(
            f"[autonomy] cartographer: proposed {proposed} refresh(es) "
            f"(in_flight={skipped_in_flight} cooldown={skipped_cooldown} "
            f"fresh={skipped_fresh})"
        )

    return {
        "proposed": proposed,
        "skipped_in_flight": skipped_in_flight,
        "skipped_cooldown": skipped_cooldown,
        "skipped_fresh": skipped_fresh,
        "details": details,
    }
