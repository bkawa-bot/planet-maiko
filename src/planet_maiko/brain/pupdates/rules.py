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
ACTION_CREATE_TASK = "create_task"
ACTION_COMPLETE_TASK = "complete_task"  # close the review/coding task linked to this PR
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
        "name": "close_task_on_pr_approved",
        "description": "Close the linked review/coding task when a PR is approved",
        "match": {"type": "pr_approved"},
        "action": ACTION_COMPLETE_TASK,
    },
    {
        "name": "close_task_on_pr_merged",
        "description": "Close the linked review/coding task + clean up its worktree when the PR merges",
        "match": {"type": "pr_merged"},
        "action": ACTION_COMPLETE_TASK,
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


def load_rules(*, include_disabled=False):
    """Load rules from: user config > plugin rules > poller rules > defaults.

    Priority order:
        1. User-defined rules in config.yaml (if set, replaces everything)
        2. Default built-in rules
        3. Plugin-registered rules (entry_points "planet_maiko.rules")
        4. Poller-provided rules (from get_rules() on each poller)

    User-disabled rules (via config.brain.disabled_rules) are filtered
    out unless include_disabled=True — the Automations dashboard passes
    that so disabled rules still render (greyed out) and can be
    re-enabled from the UI.
    """
    config = load_config()
    brain_config = config.get("brain", {})
    user_rules = brain_config.get("rules")

    if user_rules is not None:
        rules = list(user_rules)
    else:
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

    if include_disabled:
        return rules

    # User can pause any rule from the Automations dashboard without
    # having to rewrite the defaults file. Paused rules stay in the
    # full list above; we just filter them out of the live processor.
    disabled = set(brain_config.get("disabled_rules") or [])
    if disabled:
        rules = [r for r in rules if r.get("name") not in disabled]
    return rules


def is_rule_disabled(name):
    """True if the named rule is in the disabled_rules config list."""
    config = load_config()
    disabled = (config.get("brain") or {}).get("disabled_rules") or []
    return name in disabled


def set_rule_disabled(name, disabled):
    """Add/remove a rule name from config.brain.disabled_rules. Persists."""
    from planet_maiko.config import save_config
    config = load_config()
    brain_cfg = config.setdefault("brain", {})
    current = list(brain_cfg.get("disabled_rules") or [])
    if disabled and name not in current:
        current.append(name)
    elif not disabled and name in current:
        current.remove(name)
    brain_cfg["disabled_rules"] = current
    save_config(config)
    return current


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
