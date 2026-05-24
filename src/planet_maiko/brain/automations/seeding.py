"""Default Automation rows installed on every Maiko boot.

Three idempotent functions:
  - ensure_seed_rule_automations: the canonical pupdate rules (create
    review tasks on PR review request, Linear assignment → todo, close
    linked tasks on merge, etc).
  - ensure_seed_automations: the wildcard "keep overviews current"
    automation (one row, not one-per-repo).
  - ensure_plugin_default_automations: lets installed plugins seed
    their own canonical automations via register_default_automations().

All seeders match on `name` so re-running is a no-op for rows the
user already has — including ones they renamed manually.
"""

import logging

from planet_maiko.database import db
from planet_maiko.models.automation import Automation

logger = logging.getLogger(__name__)


# Default action for "something happened on a thing you care about" is
# now notify_me, not create_task_from_pupdate. The old defaults filled
# the Tasks list with rows the user didn't explicitly choose; notify
# surfaces the event on Home and the user converts to a task by hand
# if they want. Power users can still set up create_task automations
# manually, and the create_task action now supports an "Ask me before
# running" gate (creates a memo that mints the task on approval).
_RULE_SEEDS = [
    {
        "name": "Notify on PR review request",
        "description": "A teammate requested your review. Surfaces as a high-priority memo on Home.",
        "match": {"type": "pr_review_requested"},
        "action": "notify_me",
        "action_config": {
            "title": "{pupdate_title}",
            "body": "{pupdate_body}",
            "priority": "high",
            "url": "{pupdate_url}",
        },
    },
    {
        "name": "Notify on Linear assignment",
        "description": "A Linear issue was assigned to you. Surfaces as a memo on Home.",
        "match": {"type": "linear_assigned"},
        "action": "notify_me",
        "action_config": {
            "title": "{pupdate_title}",
            "body": "{pupdate_body}",
            "priority": "normal",
            "url": "{pupdate_url}",
        },
    },
    {
        "name": "Notify on Linear @-mention",
        "description": "Someone tagged you in a Linear issue or comment. Surfaces as a high-priority memo.",
        "match": {"type": "linear_mention"},
        "action": "notify_me",
        "action_config": {
            "title": "{pupdate_title}",
            "body": "{pupdate_body}",
            "priority": "high",
            "url": "{pupdate_url}",
        },
    },
    {
        "name": "Notify on Linear comment",
        "description": "New comment on a Linear issue you're subscribed to.",
        "match": {"type": "linear_comment"},
        "action": "notify_me",
        "action_config": {
            "title": "{pupdate_title}",
            "body": "{pupdate_body}",
            "priority": "normal",
            "url": "{pupdate_url}",
        },
    },
    {
        "name": "Notify on PagerDuty incident",
        "description": "An incident was assigned to you. Surfaces as a high-priority memo.",
        "match": {"type": "pagerduty_incident"},
        "action": "notify_me",
        "action_config": {
            "title": "{pupdate_title}",
            "body": "{pupdate_body}",
            "priority": "high",
            "url": "{pupdate_url}",
        },
    },
    {
        "name": "Notify on PR changes requested",
        "description": "Reviewer wants changes on your PR. Surfaces as a high-priority memo.",
        "match": {"type": "pr_changes_requested"},
        "action": "notify_me",
        "action_config": {
            "title": "{pupdate_title}",
            "body": "{pupdate_body}",
            "priority": "high",
            "url": "{pupdate_url}",
        },
    },
    {
        "name": "Notify on CI failure",
        "description": "CI is red on your PR. Surfaces as a high-priority memo.",
        "match": {"type": "pr_ci_failed"},
        "action": "notify_me",
        "action_config": {
            "title": "{pupdate_title}",
            "body": "{pupdate_body}",
            "priority": "high",
            "url": "{pupdate_url}",
        },
    },
    {
        "name": "Close linked task on PR approved",
        "description": "An approval means the review's done. Close any review/coding task pointing at this PR.",
        "match": {"type": "pr_approved"},
        "action": "complete_linked_task",
        "action_config": {},
    },
    {
        "name": "Close linked task on PR merged",
        "description": "PR merged. Close linked tasks and clean up any worktree backing them.",
        "match": {"type": "pr_merged"},
        "action": "complete_linked_task",
        "action_config": {},
    },
]


# Names from the previous default-seed set. On startup we archive any
# of these that the user still has lying around so the auto-creation
# behavior actually goes away on existing installs, not just fresh
# ones. Idempotent: if the user already renamed or archived a row, the
# query just doesn't find it.
_OBSOLETE_SEED_NAMES = [
    "Create task on PR review request",
    "Create task on Linear assignment",
    "Create task on PagerDuty incident",
    "Create task on PR changes requested",
    "Create task on CI failure",
]


def migrate_obsolete_create_task_seeds():
    """Archive the old `create_task_from_pupdate` seeded rows so the
    new notify_me defaults take over without firing both side-by-side.

    Only touches rows that are still flagged `created_by='seed'` and
    haven't been archived already. If the user renamed a row or moved
    it off seed status, we leave it alone."""
    archived = 0
    rows = (
        Automation.query
        .filter(Automation.name.in_(_OBSOLETE_SEED_NAMES))
        .filter(Automation.created_by == "seed")
        .filter(Automation.status != "archived")
        .all()
    )
    for row in rows:
        row.status = "archived"
        archived += 1
    if archived:
        db.session.commit()
        logger.info(
            f"[automations] archived {archived} obsolete create_task "
            f"default rule(s) — replaced by notify_me equivalents"
        )
    return archived


def ensure_seed_rule_automations():
    """Seed pupdate-scope Automations for the canonical matchers.
    Idempotent on (name, execution_scope).
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


def ensure_seed_automations():
    """Install the canonical "keep overviews current" automation.

    Seeds exactly one wildcard automation: overview_stale with no
    scope iterates every configured repo each cycle, and
    run_agent_job's repo fallback chain picks up the matched repo
    from context.

    Idempotent: the wildcard row has a stable name so re-runs are
    no-ops. Stray per-repo seeds are archived by
    migrate_per_repo_overview_watches() on startup (separate function
    so the user can opt out by editing one of them manually).
    """
    from planet_maiko.config import load_config

    config = load_config()
    repos = (config.get("github") or {}).get("repos") or []
    if not repos:
        # Still seed the wildcard — it's inert with no repos configured
        # but means adding a repo later doesn't require another seed pass.
        pass

    cart_cfg = (
        ((config.get("brain") or {}).get("role_autonomy") or {}).get("cartographer") or {}
    )
    stale_days = int(cart_cfg.get("stale_days", 30))
    cooldown_days = int(cart_cfg.get("cooldown_days", 7))

    wildcard_name = "Keep repo overviews current"
    existing = (
        Automation.query
        .filter(Automation.name == wildcard_name)
        .filter(Automation.created_by == "seed")
        .first()
    )
    if existing:
        return 0

    a = Automation(
        name=wildcard_name,
        description=(
            "Atlas re-cartographs any configured repo whose Repo Overview "
            f"insight is missing or older than {stale_days} days. One row "
            "covers every repo — the condition picks whichever is stale "
            "first and spawns Atlas for it. Approving the proposal kicks "
            "off the cartographer run."
        ),
        when=[{
            "kind": "overview_stale",
            # Empty repo = wildcard — condition iterates every repo in
            # config.github.repos and fires on the first stale one.
            "config": {"repo": "", "stale_days": stale_days},
        }],
        when_logic="all",
        then=[{
            "kind": "run_agent_job",
            "config": {
                "ask_first": True,
                "kind": "cartograph",
                "title": "Cartograph {repo}",
                "priority": "normal",
                # scope_repo falls back to context.repo from the
                # condition automatically — no need to template here.
                "description": (
                    "{repo} hasn't been cartographed in a while. "
                    "Approving spawns Atlas to walk the tree and "
                    "produce a fresh Repo Overview."
                ),
            },
        }],
        status="active",
        created_by="seed",
        scope_repo=None,
        cooldown_days=cooldown_days,
    )
    db.session.add(a)
    db.session.commit()
    logger.info("[automations] seeded 1 wildcard overview watch")
    return 1


def ensure_plugin_default_automations():
    """Install automations that plugins declare via
    `register_default_automations()`.

    Idempotent: each seeded row carries `created_by="plugin:<name>"`
    and is uniquely identified within the plugin by the `seed_key`
    field stored in the row's description prefix. Re-running this
    finds the existing row and leaves it alone — so editing a seeded
    automation from the UI won't have it overwritten on next boot.

    Seeded rows are regular Automations — the user can pause, edit,
    or archive them from the Automations page like any other.
    """
    from planet_maiko.plugins.loader import get_plugins

    created = 0
    for plugin in get_plugins():
        try:
            entries = plugin.register_default_automations() or []
        except Exception as e:
            logger.warning(
                f"[automations] plugin '{plugin.name}' "
                f"register_default_automations failed: {e}"
            )
            continue

        created_by = f"plugin:{plugin.name}"
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            seed_key = raw.get("seed_key") or raw.get("name")
            if not seed_key:
                continue

            existing = (
                Automation.query
                .filter(Automation.created_by == created_by)
                .all()
            )
            already_seeded = any(
                (a.description or "").startswith(f"[seed:{seed_key}]")
                for a in existing
            )
            if already_seeded:
                continue

            name = raw.get("name") or seed_key
            desc_body = raw.get("description") or ""
            # Prefix the description with a machine tag so we can
            # find the row later even if the user renamed it. The
            # Automations UI just shows the description as-is so
            # the tag is visible — low cost for idempotence.
            description = f"[seed:{seed_key}] {desc_body}".strip()

            a = Automation(
                name=name,
                description=description,
                when=raw.get("when") or [],
                when_logic=raw.get("when_logic") or "all",
                within_minutes=raw.get("within_minutes"),
                then=raw.get("then") or [],
                status=raw.get("status") or "active",
                created_by=created_by,
                scope_repo=raw.get("scope_repo"),
                execution_scope=raw.get("execution_scope") or "cycle",
                cooldown_days=int(raw.get("cooldown_days") or 7),
            )
            db.session.add(a)
            created += 1
            logger.info(
                f"[automations] plugin '{plugin.name}' seeded automation "
                f"'{name}' (seed_key={seed_key})"
            )

    if created:
        db.session.commit()
    return created

