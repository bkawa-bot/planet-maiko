"""Pupdate rules - the instruction set for the brain's pupdate processor.

Each rule defines:
    - match: conditions that a pupdate must satisfy
    - action: what to do when matched (dismiss, mark_read, create_task)
    - description: human-readable explanation

Rules are evaluated in order. The first matching rule wins.
If no rule matches, the pupdate is left unprocessed for manual triage.
"""

import logging
from planet_maiko.config import load_config

logger = logging.getLogger(__name__)

# Actions the processor can execute
ACTION_DISMISS = "dismiss"
ACTION_MARK_READ = "mark_read"
ACTION_CREATE_TASK = "create_task"
ACTION_SKIP = "skip"  # explicitly skip (don't process, leave for user)


def _matches(pupdate, conditions):
    """Check if a pupdate matches all conditions in a rule.

    Supported condition keys:
        source: exact match on source (e.g. "github")
        type: exact match on type (e.g. "pr_approved")
        type_prefix: type starts with value (e.g. "pr_")
        priority: exact match on priority
        priority_in: priority is one of the listed values
        actionable: boolean match
        has_tag: pupdate has this tag
        title_contains: case-insensitive substring match on title
    """
    for key, expected in conditions.items():
        if key == "source" and pupdate.source != expected:
            return False
        elif key == "type" and pupdate.type != expected:
            return False
        elif key == "type_prefix" and not pupdate.type.startswith(expected):
            return False
        elif key == "priority" and pupdate.priority != expected:
            return False
        elif key == "priority_in" and pupdate.priority not in expected:
            return False
        elif key == "actionable" and pupdate.actionable != expected:
            return False
        elif key == "has_tag" and expected not in (pupdate.tags or []):
            return False
        elif key == "title_contains" and expected.lower() not in (pupdate.title or "").lower():
            return False
    return True


# Built-in default rules (applied if user hasn't overridden)
DEFAULT_RULES = [
    {
        "name": "skip_agent_ready",
        "description": "Mark agent-ready notifications as read (informational)",
        "match": {"type": "agent_ready"},
        "action": ACTION_MARK_READ,
    },
    {
        "name": "auto_dismiss_ci_pass",
        "description": "Auto-dismiss CI passing notifications",
        "match": {"type": "pr_ci_passed"},
        "action": ACTION_DISMISS,
    },
    {
        "name": "auto_dismiss_bot_prs",
        "description": "Auto-dismiss PRs from bots (dependabot, renovate)",
        "match": {"type_prefix": "pr_", "title_contains": "dependabot"},
        "action": ACTION_DISMISS,
    },
    {
        "name": "task_from_review_request",
        "description": "Create a task when a PR review is requested",
        "match": {"type": "pr_review_requested"},
        "action": ACTION_CREATE_TASK,
        "task_type": "review",
        "task_priority": "high",
    },
    {
        "name": "task_from_linear_assignment",
        "description": "Create a task when a Linear issue is assigned",
        "match": {"type": "linear_assigned"},
        "action": ACTION_CREATE_TASK,
        "task_type": "todo",
    },
    {
        "name": "task_from_changes_requested",
        "description": "Create a task when PR changes are requested",
        "match": {"type": "pr_changes_requested"},
        "action": ACTION_CREATE_TASK,
        "task_type": "bug",
        "task_priority": "high",
    },
    {
        "name": "task_from_ci_failure",
        "description": "Create a task when CI fails on your PR",
        "match": {"type": "pr_ci_failed"},
        "action": ACTION_CREATE_TASK,
        "task_type": "bug",
        "task_priority": "high",
    },
    {
        "name": "read_approved_prs",
        "description": "Mark approved PRs as read and complete review task",
        "match": {"type": "pr_approved"},
        "action": ACTION_MARK_READ,
    },
    {
        "name": "read_merged_prs",
        "description": "Mark merged PRs as read and complete review task",
        "match": {"type": "pr_merged"},
        "action": ACTION_MARK_READ,
    },
    {
        "name": "read_calendar_events",
        "description": "Mark calendar events as read",
        "match": {"source": "calendar"},
        "action": ACTION_MARK_READ,
    },
]


def _discover_plugin_rules():
    """Discover rules registered by plugins via entry_points."""
    from importlib.metadata import entry_points
    plugin_rules = []
    try:
        eps = entry_points(group="planet_maiko.rules")
        for ep in eps:
            try:
                rules = ep.load()
                if isinstance(rules, list):
                    plugin_rules.extend(rules)
                    logger.info(f"[rules] Loaded {len(rules)} rule(s) from plugin '{ep.name}'")
            except Exception as e:
                logger.warning(f"[rules] Failed to load rules from '{ep.name}': {e}")
    except Exception as e:
        logger.debug(f"[rules] No plugin rules entry points: {e}")
    return plugin_rules


def _discover_poller_rules():
    """Discover rules from installed pollers via get_rules()."""
    from importlib.metadata import entry_points
    poller_rules = []
    try:
        eps = entry_points(group="planet_maiko.pollers")
        for ep in eps:
            try:
                poller_cls = ep.load()
                poller = poller_cls()
                rules = poller.get_rules()
                if rules:
                    poller_rules.extend(rules)
            except Exception as e:
                logger.warning(f"[rules] Failed to load rules from poller '{ep.name}': {e}")
    except Exception as e:
        logger.debug(f"[rules] No poller rules entry points: {e}")
    return poller_rules


def load_rules():
    """Load rules from: user config > plugin rules > poller rules > defaults.

    Priority order:
        1. User-defined rules in config.yaml (if set, replaces everything)
        2. Default built-in rules
        3. Plugin-registered rules (entry_points "planet_maiko.rules")
        4. Poller-provided rules (from get_rules() on each poller)
    """
    config = load_config()
    brain_config = config.get("brain", {})
    user_rules = brain_config.get("rules")

    if user_rules is not None:
        return user_rules

    rules = list(DEFAULT_RULES)

    # Merge plugin rules
    plugin_rules = _discover_plugin_rules()
    poller_rules = _discover_poller_rules()

    # Deduplicate by name
    seen = {r["name"] for r in rules}
    for r in plugin_rules + poller_rules:
        if r.get("name") and r["name"] not in seen:
            rules.append(r)
            seen.add(r["name"])

    return rules


def evaluate(pupdate, rules=None):
    """Evaluate a pupdate against all rules.

    Returns:
        The first matching rule dict, or None if no rule matches.
    """
    if rules is None:
        rules = load_rules()

    for rule in rules:
        conditions = rule.get("match", {})
        if _matches(pupdate, conditions):
            logger.debug(f"Pupdate {pupdate.id} matched rule '{rule.get('name', '?')}'")
            return rule

    return None
