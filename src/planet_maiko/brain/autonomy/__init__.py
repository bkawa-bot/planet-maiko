"""Role-as-intent autonomy — goal-driven proposals from role-native agents.

Stage 1: the hardcoded cartographer detector from Stage 0 has been
generalized into a goal-driven evaluator backed by `AgentGoal` rows.
Each goal row represents a durable intent ("keep planet-maiko's
overview current") held by a role, and the evaluator dispatches on
`goal.kind` to the matching detector function.

Flow per cycle:
  1. ensure_seed_goals() — for every configured repo, make sure a
     keep_overview_current goal exists. Idempotent.
  2. evaluate() — iterate every active goal, run its detector, and
     emit proposals as needed. Paused / archived goals are skipped.

Adding a new goal kind = adding a new detector function + wiring it
into the dispatch table in `_DETECTORS`. No schema changes needed.

Proposal → task → agent path is unchanged from Stage 0: proposals
land in the inbox as `agent_proposal` pupdates and run through
`approve_proposal` + `route` + `_phase_execute_agent_tasks`.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.insight import Insight
from planet_maiko.models.agent_goal import AgentGoal

logger = logging.getLogger(__name__)


_TAG_PREFIX = "role_trigger"


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------

def ensure_seed_goals():
    """Ensure one AgentGoal row exists per configured repo, per seed kind.

    Idempotent. Safe to call every cycle — if the user adds a new repo
    to config.github.repos, the next tick seeds a goal for it without
    needing a restart or a separate migration step.

    Only seeds with defaults from brain.role_autonomy config — so a
    user-tuned stale_days picks up on the next reseed for any NEW
    goals, while existing goals keep their current config.
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

    created = 0
    for repo in repos:
        exists = (
            AgentGoal.query
            .filter(AgentGoal.role == "cartographer")
            .filter(AgentGoal.kind == "keep_overview_current")
            .filter(AgentGoal.scope_repo == repo)
            .first()
        )
        if exists:
            continue
        goal = AgentGoal(
            role="cartographer",
            agent_profile_id=None,
            kind="keep_overview_current",
            scope_repo=repo,
            trigger_kind="condition",
            trigger_config={"stale_days": stale_days},
            action_kind="propose",
            action_config={},
            status="active",
            created_by="seed",
            extra={
                "description": f"Keep {repo}'s Repo Overview current so agents starting on this repo inherit an accurate map via CLAUDE.md.",
            },
        )
        db.session.add(goal)
        created += 1

    if created:
        db.session.commit()
        logger.info(f"[autonomy] seeded {created} goal(s)")
    return created


# --------------------------------------------------------------------------
# Dedup helpers (shared across detectors)
# --------------------------------------------------------------------------

def _tag_for(goal):
    """Stable dedup tag for proposals emitted by this goal."""
    return f"{_TAG_PREFIX}:{goal.kind}:{goal.scope_repo or 'global'}"


def _pupdate_in_flight(tag):
    return (
        Pupdate.query
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .filter(Pupdate.tags.contains(tag))
        .first()
    ) is not None


def _cooldown_active(tag, cooldown_days):
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


# --------------------------------------------------------------------------
# Detectors — one per goal.kind. Each returns a dict describing outcome.
# --------------------------------------------------------------------------

def _detect_keep_overview_current(goal):
    """Fires when the repo's Repo Overview insight is missing or older
    than trigger_config.stale_days. Emits an agent_proposal pupdate
    shaped like CartographLauncher produces.
    """
    repo = goal.scope_repo
    if not repo:
        return {"outcome": "skip", "reason": "no_scope_repo"}

    stale_days = int((goal.trigger_config or {}).get("stale_days", 30))
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=stale_days)

    overview = _latest_overview_for_repo(repo)
    if overview is None:
        _emit_cartograph_proposal(goal, repo, reason="missing")
        return {"outcome": "proposed", "reason": "missing"}

    last_confirmed = overview.last_confirmed_at
    if last_confirmed and last_confirmed.tzinfo is None:
        last_confirmed = last_confirmed.replace(tzinfo=timezone.utc)
    if last_confirmed is None or last_confirmed < stale_cutoff:
        age_days = None
        if last_confirmed is not None:
            age_days = int((now - last_confirmed).total_seconds() // 86400)
        _emit_cartograph_proposal(
            goal, repo, reason="stale", overview_age_days=age_days,
        )
        return {"outcome": "proposed", "reason": "stale", "age_days": age_days}

    return {"outcome": "skip", "reason": "fresh"}


def _latest_overview_for_repo(repo):
    """Most recent ACTIVE overview insight for the given repo, or None."""
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


def _emit_cartograph_proposal(goal, repo, *, reason, overview_age_days=None):
    tag = _tag_for(goal)
    pupdate_id = f"role-trigger-cartograph-{repo.replace('/', '_')}-{uuid.uuid4().hex[:6]}"

    if reason == "missing":
        title = f"Cartograph {repo}? No overview on file."
        body = (
            f"{repo} has never been cartographed. Approving spawns Atlas "
            f"to walk the tree and draft a Repo Overview insight for it. "
            f"Overviews inject into every agent's CLAUDE.md so new pups "
            f"start with the lay of the land."
        )
    else:
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
        source_id=f"role-trigger/{goal.kind}/{repo}",
        type="agent_proposal",
        priority="low",
        title=title,
        body=body,
        actionable=True,
        action_hint="Approve / dismiss",
        tags=["proposal", "from_maiko", tag],
        extra={
            "from_agent_id": None,
            "draft": {
                "title": f"Cartograph {repo}",
                "type": "cartograph",
                "priority": "normal",
                "repo": repo,
                "description": body,
            },
            "role_trigger": {
                "goal_id": goal.id,
                "kind": goal.kind,
                "repo": repo,
                "reason": reason,
                "overview_age_days": overview_age_days,
            },
        },
        brain_processed=True,
    )
    db.session.add(pupdate)
    return pupdate


def _detect_train_lora_when_ready(goal):
    """Fires when a repo has accumulated enough active Learnings to be
    worth training a LoRA on, and no profile for that repo yet has an
    adapter path set.

    Output: a low-priority `maiko_nudge` pupdate pointing at the Training
    page. This isn't an agent_proposal because there's no Maiko-spawnable
    task that runs LoRA training — training is an explicit user action
    via /knowledge?tab=training. So the nudge is "conditions are right,
    here's the page" rather than "approve this task".

    The goal itself holds the threshold + scope_repo so the user can
    tune it per repo from the UI later.
    """
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.agent_profile import AgentProfile

    repo = goal.scope_repo
    if not repo:
        return {"outcome": "skip", "reason": "no_scope_repo"}

    min_learnings = int((goal.trigger_config or {}).get("min_learnings", 10))

    active_count = (
        Learning.query
        .filter(Learning.status == "active")
        .filter(Learning.scope_repo == repo)
        .count()
    )
    if active_count < min_learnings:
        return {"outcome": "skip", "reason": "below_threshold", "count": active_count}

    # Any profile for this repo already carry an adapter? If so, nothing
    # to nudge about — training happened. The goal keeps watching so a
    # later rule-growth uptick can re-nudge for a refreshed adapter.
    has_adapter = False
    for profile in AgentProfile.query.filter(AgentProfile.scope_repo == repo).all():
        if (profile.extra or {}).get("adapter_path"):
            has_adapter = True
            break
    if has_adapter:
        return {"outcome": "skip", "reason": "adapter_exists"}

    tag = _tag_for(goal)
    pupdate_id = f"role-trigger-train-lora-{repo.replace('/', '_')}-{uuid.uuid4().hex[:6]}"
    title = f"Ready to train a LoRA for {repo}?"
    body = (
        f"{repo} has {active_count} active learnings and no trained "
        f"adapter yet. A LoRA trained on these rules reviews commits "
        f"at check_code() time so violations surface before ready_for_review. "
        f"Open the Training page to kick off a run."
    )
    pupdate = Pupdate(
        id=pupdate_id,
        source="maiko",
        source_id=f"role-trigger/{goal.kind}/{repo}",
        type="maiko_nudge",
        priority="low",
        title=title,
        body=body,
        url="/knowledge?tab=training",
        actionable=True,
        action_hint="Open Training",
        tags=["nudge", "from_maiko", tag],
        extra={
            "role_trigger": {
                "goal_id": goal.id,
                "kind": goal.kind,
                "repo": repo,
                "active_learnings": active_count,
            },
        },
        brain_processed=True,
    )
    db.session.add(pupdate)
    return {"outcome": "proposed", "active_learnings": active_count}


# Dispatch table — add new detectors here keyed by goal.kind.
_DETECTORS = {
    "keep_overview_current": _detect_keep_overview_current,
    "train_lora_when_ready": _detect_train_lora_when_ready,
}


# --------------------------------------------------------------------------
# Gap detection — noticing coverage gaps and proposing *new goals*
# --------------------------------------------------------------------------
#
# Stage 2 surface. Unlike goal evaluation (which fires proposals *from*
# existing active goals), gap detection notices conditions that warrant
# a new standing intent and emits an agent_proposal carrying an
# extra.proposed_goal blob. Approving-as-goal installs the goal row;
# dismissing cools it down so the user isn't nagged.
#
# Each gap detector returns a list of proposal specs (not pupdates —
# the orchestrator decides dedup + cap). A spec is a dict:
#   {
#     "tag": "gap:<gap_kind>:<repo>",   # dedup
#     "title": str, "body": str,
#     "proposed_goal": { ...AgentGoal-shaped fields... },
#   }

def _gap_tag(gap_kind, repo):
    return f"gap:{gap_kind}:{repo}"


def _goal_already_exists(kind, scope_repo):
    """True if a non-archived AgentGoal of the given (kind, scope_repo)
    is already installed. Prevents re-proposing a gap that the user has
    already adopted (or explicitly paused — paused goals are "I know
    about this, quiet for now", not "please re-suggest").
    """
    exists = (
        AgentGoal.query
        .filter(AgentGoal.kind == kind)
        .filter(AgentGoal.scope_repo == scope_repo)
        .filter(AgentGoal.status != "archived")
        .first()
    )
    return exists is not None


def _detect_gap_lora_missing_for_busy_repo():
    """For each configured repo, if there are enough active Learnings
    to make a LoRA worth training AND no profile for the repo has an
    adapter_path yet AND no existing train_lora_when_ready goal covers
    this repo — emit a gap proposal.
    """
    from planet_maiko.config import load_config
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.agent_profile import AgentProfile

    config = load_config()
    gap_cfg = (
        ((config.get("brain") or {}).get("role_autonomy") or {}).get("gap_detection") or {}
    )
    detector_cfg = gap_cfg.get("lora_missing") or {}
    min_learnings = int(detector_cfg.get("min_active_learnings", 10))

    repos = (config.get("github") or {}).get("repos") or []
    specs = []
    for repo in repos:
        if _goal_already_exists("train_lora_when_ready", repo):
            continue

        active_count = (
            Learning.query
            .filter(Learning.status == "active")
            .filter(Learning.scope_repo == repo)
            .count()
        )
        if active_count < min_learnings:
            continue

        has_adapter = False
        for profile in AgentProfile.query.filter(AgentProfile.scope_repo == repo).all():
            if (profile.extra or {}).get("adapter_path"):
                has_adapter = True
                break
        if has_adapter:
            continue

        specs.append({
            "tag": _gap_tag("lora_missing", repo),
            "title": f"Watch {repo} for when a LoRA is worth training?",
            "body": (
                f"{repo} has {active_count} active learnings — more than "
                f"the {min_learnings}-rule threshold — and no trained adapter "
                f"yet. Approving installs a standing goal that nudges you "
                f"when training is worth doing (you still kick it off from "
                f"the Training page; Maiko just reminds you when the time's "
                f"right)."
            ),
            "proposed_goal": {
                "role": "coding",
                "kind": "train_lora_when_ready",
                "scope_repo": repo,
                "trigger_kind": "condition",
                "trigger_config": {"min_learnings": min_learnings},
                "action_kind": "propose",
                "action_config": {},
                "extra": {
                    "description": f"Nudge when {repo} has ≥{min_learnings} rules and no adapter.",
                },
            },
        })
    return specs


# Gap detectors — each returns a list of proposal specs.
_GAP_DETECTORS = [
    _detect_gap_lora_missing_for_busy_repo,
]


def detect_gaps():
    """Run every gap detector, dedup/cooldown/cap, and emit goal proposals.

    Independent of evaluate() so operational issues in one path don't
    poison the other (a detector that throws won't starve goal firing).

    Proposals created here are agent_proposal pupdates with a
    `proposed_goal` blob in extra — the frontend renders them with a
    different CTA ("Adopt goal" instead of "Create task") and they hit
    the approve-as-goal endpoint rather than approve-proposal.
    """
    from planet_maiko.config import load_config

    config = load_config()
    gap_cfg = (
        ((config.get("brain") or {}).get("role_autonomy") or {}).get("gap_detection") or {}
    )
    if not gap_cfg.get("enabled", False):
        return {"proposed": 0, "reason": "disabled"}

    cooldown_days = int(gap_cfg.get("cooldown_days", 14))
    max_per_cycle = int(gap_cfg.get("max_proposals_per_cycle", 2))

    specs = []
    for detector in _GAP_DETECTORS:
        try:
            specs.extend(detector() or [])
        except Exception as e:
            logger.warning(f"[autonomy] gap detector {detector.__name__} failed: {e}")

    proposed = 0
    skipped_in_flight = 0
    skipped_cooldown = 0
    skipped_capped = 0
    details = []

    for spec in specs:
        if proposed >= max_per_cycle:
            skipped_capped += 1
            continue
        tag = spec["tag"]
        if _pupdate_in_flight(tag):
            skipped_in_flight += 1
            details.append({"tag": tag, "outcome": "in_flight"})
            continue
        if _cooldown_active(tag, cooldown_days):
            skipped_cooldown += 1
            details.append({"tag": tag, "outcome": "cooldown"})
            continue

        pupdate_id = f"gap-proposal-{uuid.uuid4().hex[:10]}"
        pupdate = Pupdate(
            id=pupdate_id,
            source="maiko",
            source_id=spec["tag"],
            type="agent_proposal",
            priority="low",
            title=spec["title"],
            body=spec["body"],
            actionable=True,
            action_hint="Adopt goal / dismiss",
            tags=["proposal", "from_maiko", "goal_proposal", tag],
            extra={
                "from_agent_id": None,
                "proposed_goal": spec["proposed_goal"],
            },
            brain_processed=True,
        )
        db.session.add(pupdate)
        proposed += 1
        details.append({"tag": tag, "outcome": "proposed"})

    if proposed:
        db.session.commit()
        logger.info(
            f"[autonomy] gap-proposed {proposed} (in_flight={skipped_in_flight} "
            f"cooldown={skipped_cooldown} capped={skipped_capped})"
        )

    return {
        "proposed": proposed,
        "skipped_in_flight": skipped_in_flight,
        "skipped_cooldown": skipped_cooldown,
        "skipped_capped": skipped_capped,
        "details": details,
    }


# --------------------------------------------------------------------------
# Top-level evaluator
# --------------------------------------------------------------------------

def evaluate():
    """Run every active goal through its detector and emit proposals.

    Safe to call on every brain cycle:
      - Ensures goals are seeded for the current repo list first.
      - Skips paused/archived goals.
      - Dedup + cooldown per goal prevent repeat proposals.
      - Respects per-kind caps from brain.role_autonomy config so a
        fresh install doesn't flood the inbox on the first tick.

    Returns:
        dict with per-kind counts + a details list for log triage.
    """
    from planet_maiko.config import load_config

    config = load_config()
    autonomy_cfg = (config.get("brain") or {}).get("role_autonomy") or {}
    cartographer_cfg = autonomy_cfg.get("cartographer") or {}

    # Master kill switch: the cartographer config's `enabled` flag also
    # gates the whole phase, matching Stage 0's behavior. If any role
    # is enabled we run; each goal still gets filtered by its own
    # role-config below.
    any_enabled = any(
        (autonomy_cfg.get(role_key) or {}).get("enabled", False)
        for role_key in autonomy_cfg.keys()
    )
    if not any_enabled:
        return {"proposed": 0, "reason": "disabled"}

    # Reseed before evaluating so new repos show up as goals the same
    # cycle the user adds them in Settings.
    try:
        ensure_seed_goals()
    except Exception as e:
        logger.warning(f"[autonomy] seed failed: {e}")

    goals = (
        AgentGoal.query
        .filter(AgentGoal.status == "active")
        .order_by(AgentGoal.id.asc())
        .all()
    )

    # Per-role caps. Stage 0's cap was "cartographer emits at most N
    # proposals per cycle"; preserve that shape so a user with 20 repos
    # doesn't see 20 proposals land at once on a fresh install.
    caps = {}
    emitted_by_role = {}
    for role_key, role_cfg in autonomy_cfg.items():
        if not (role_cfg or {}).get("enabled", False):
            caps[role_key] = 0
        else:
            caps[role_key] = int((role_cfg or {}).get("max_proposals_per_cycle", 2))
        emitted_by_role[role_key] = 0

    proposed = 0
    skipped_in_flight = 0
    skipped_cooldown = 0
    skipped_fresh = 0
    skipped_capped = 0
    skipped_unknown_kind = 0
    details = []

    for goal in goals:
        role_key = goal.role
        # Check the role's master enable flag.
        role_cfg = autonomy_cfg.get(role_key) or {}
        if not role_cfg.get("enabled", False):
            details.append({"goal_id": goal.id, "outcome": "role_disabled"})
            continue

        if emitted_by_role.get(role_key, 0) >= caps.get(role_key, 0):
            skipped_capped += 1
            details.append({"goal_id": goal.id, "outcome": "capped"})
            continue

        detector = _DETECTORS.get(goal.kind)
        if detector is None:
            skipped_unknown_kind += 1
            details.append({"goal_id": goal.id, "outcome": "unknown_kind"})
            continue

        tag = _tag_for(goal)
        if _pupdate_in_flight(tag):
            skipped_in_flight += 1
            details.append({"goal_id": goal.id, "outcome": "in_flight"})
            continue
        cooldown_days = int(role_cfg.get("cooldown_days", 7))
        if _cooldown_active(tag, cooldown_days):
            skipped_cooldown += 1
            details.append({"goal_id": goal.id, "outcome": "cooldown"})
            continue

        try:
            result = detector(goal)
        except Exception as e:
            logger.warning(f"[autonomy] detector {goal.kind} failed for goal {goal.id}: {e}")
            details.append({"goal_id": goal.id, "outcome": "error", "error": str(e)})
            continue

        outcome = (result or {}).get("outcome")
        if outcome == "proposed":
            proposed += 1
            emitted_by_role[role_key] = emitted_by_role.get(role_key, 0) + 1
            goal.last_fired_at = datetime.now(timezone.utc)
            details.append({"goal_id": goal.id, "outcome": "proposed", "detector": result})
        else:
            skipped_fresh += 1
            details.append({"goal_id": goal.id, "outcome": "fresh", "detector": result})

    if proposed:
        db.session.commit()
        logger.info(
            f"[autonomy] proposed {proposed} (in_flight={skipped_in_flight} "
            f"cooldown={skipped_cooldown} fresh={skipped_fresh} capped={skipped_capped})"
        )

    return {
        "proposed": proposed,
        "skipped_in_flight": skipped_in_flight,
        "skipped_cooldown": skipped_cooldown,
        "skipped_fresh": skipped_fresh,
        "skipped_capped": skipped_capped,
        "skipped_unknown_kind": skipped_unknown_kind,
        "details": details,
    }
