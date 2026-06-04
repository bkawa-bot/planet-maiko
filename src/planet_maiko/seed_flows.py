"""Default Flows seeded on boot — the out-of-box equivalents of the canonical
pupdate automations, expressed as the one control-flow primitive (flows).

Each is a 2-node flow: a Pupdate trigger wired to an action. Seeding archives
the matching seeded automation so the same event doesn't fire twice (once via
the old automation engine, once via the flow). Idempotent on extra.seed_key;
the archive only touches automations still flagged created_by="seed", so a row
the user renamed or edited is left alone.

Armed by default (these are the out-of-box behaviors). A freshly-armed pupdate
flow consumes the existing backlog silently on its first eval, so seeding does
NOT retro-fire on old pupdates — only on new ones.
"""

import logging

logger = logging.getLogger(__name__)


def _pupdate_to_action_flow(pupdate_type, action_config):
    """A 2-node flow: [pupdate trigger: type] -> [action]. The node shape
    matches what the editor serializes (kind + agent_type + config), so a
    seeded flow opens and edits like a hand-built one."""
    return {
        "nodes": [
            {
                "id": "trigger", "x": 80, "y": 110,
                "kind": "trigger", "agent_type": "trigger",
                "config": {"trigger_kind": "pupdate", "pupdate_type": pupdate_type},
            },
            {
                "id": "action", "x": 360, "y": 110,
                "kind": "action", "agent_type": "action",
                "config": action_config,
            },
        ],
        "edges": [{
            "id": "trigger__action", "source": "trigger", "target": "action",
            "sourceHandle": "out", "targetHandle": "in",
        }],
    }


def _notify(pupdate_type, priority):
    return _pupdate_to_action_flow(
        pupdate_type, {"subtype": "create_memo", "priority": priority},
    )


def _close_task(pupdate_type):
    return _pupdate_to_action_flow(
        pupdate_type, {"subtype": "complete_linked_task"},
    )


# The canonical defaults, 1:1 with the pupdate rules in
# brain/automations/seeding.py (_RULE_SEEDS).
_FLOW_SEEDS = [
    {"seed_key": "notify_pr_review_requested",
     "name": "Notify on PR review request",
     "description": "A teammate requested your review. Surfaces as a high-priority memo on Home.",
     "graph": _notify("pr_review_requested", "high")},
    {"seed_key": "notify_linear_assigned",
     "name": "Notify on Linear assignment",
     "description": "A Linear issue was assigned to you. Surfaces as a memo on Home.",
     "graph": _notify("linear_assigned", "normal")},
    {"seed_key": "notify_linear_mention",
     "name": "Notify on Linear @-mention",
     "description": "Someone tagged you in a Linear issue or comment. Surfaces as a high-priority memo.",
     "graph": _notify("linear_mention", "high")},
    {"seed_key": "notify_linear_comment",
     "name": "Notify on Linear comment",
     "description": "New comment on a Linear issue you're subscribed to.",
     "graph": _notify("linear_comment", "normal")},
    {"seed_key": "notify_pagerduty_incident",
     "name": "Notify on PagerDuty incident",
     "description": "An incident was assigned to you. Surfaces as a high-priority memo.",
     "graph": _notify("pagerduty_incident", "high")},
    {"seed_key": "notify_pr_changes_requested",
     "name": "Notify on PR changes requested",
     "description": "Reviewer wants changes on your PR. Surfaces as a high-priority memo.",
     "graph": _notify("pr_changes_requested", "high")},
    {"seed_key": "notify_pr_ci_failed",
     "name": "Notify on CI failure",
     "description": "CI is red on your PR. Surfaces as a high-priority memo.",
     "graph": _notify("pr_ci_failed", "high")},
    {"seed_key": "close_task_pr_approved",
     "name": "Close linked task on PR approved",
     "description": "An approval means the review's done. Close any review/coding task pointing at this PR.",
     "graph": _close_task("pr_approved")},
    {"seed_key": "close_task_pr_merged",
     "name": "Close linked task on PR merged",
     "description": "PR merged. Close linked tasks and clean up any worktree backing them.",
     "graph": _close_task("pr_merged")},
]

# The seeded automations these flows replace. Archived on seed so the same
# event doesn't fire on both engines; only untouched created_by="seed" rows.
_REPLACED_AUTOMATION_NAMES = [
    "Notify on PR review request",
    "Notify on Linear assignment",
    "Notify on Linear @-mention",
    "Notify on Linear comment",
    "Notify on PagerDuty incident",
    "Notify on PR changes requested",
    "Notify on CI failure",
    "Close linked task on PR approved",
    "Close linked task on PR merged",
]


def ensure_seed_flows():
    """Seed the default flows (idempotent on extra.seed_key) and archive the
    seeded automations they replace. Returns the count created."""
    from planet_maiko.database import db
    from planet_maiko.models.workflow import Workflow

    # Scan ALL rows, including soft-deleted ones: if the user deleted a seeded
    # flow, its row still carries the seed_key, so we must NOT resurrect it.
    existing_keys = set()
    for w in Workflow.query.all():
        key = (w.extra or {}).get("seed_key")
        if key:
            existing_keys.add(key)

    created = 0
    for seed in _FLOW_SEEDS:
        if seed["seed_key"] in existing_keys:
            continue
        db.session.add(Workflow(
            name=seed["name"],
            description=seed["description"],
            graph=seed["graph"],
            trigger_armed=True,
            extra={"seed_key": seed["seed_key"], "created_by": "seed"},
        ))
        created += 1
    if created:
        db.session.commit()
        logger.info(f"[flows] seeded {created} default flow(s)")

    _archive_replaced_automations()
    return created


def _archive_replaced_automations():
    """Archive the seeded automations now covered by default flows, so an event
    doesn't notify twice. Mirrors migrate_obsolete_create_task_seeds: only
    rows still created_by='seed' and not already archived."""
    from planet_maiko.database import db
    from planet_maiko.models.automation import Automation
    rows = (
        Automation.query
        .filter(Automation.name.in_(_REPLACED_AUTOMATION_NAMES))
        .filter(Automation.created_by == "seed")
        .filter(Automation.status != "archived")
        .all()
    )
    archived = 0
    for row in rows:
        row.status = "archived"
        archived += 1
    if archived:
        db.session.commit()
        logger.info(
            f"[flows] archived {archived} seeded automation(s) now covered by "
            f"default flows"
        )
    return archived
