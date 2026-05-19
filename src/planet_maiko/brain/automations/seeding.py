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


_RULE_SEEDS = [
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
        "name": "Notify on Linear @-mention",
        "description": "Someone tagged you in a Linear issue or comment — surface as a high-priority memo so you don't miss it.",
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
        "description": "New comment on a Linear issue you're subscribed to — surface as an info memo.",
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
        "name": "Create task on PagerDuty incident",
        "description": "An incident assigned to you becomes a high-priority bug task.",
        "match": {"type": "pagerduty_incident"},
        "action": "create_task_from_pupdate",
        "action_config": {"task_type": "bug", "task_priority": "high"},
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

