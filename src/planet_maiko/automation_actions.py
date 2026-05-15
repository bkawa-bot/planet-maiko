"""Automation-action registry.

Symmetric with pupdate_types.py. That centralises the "when" types the
Automation editor offers; this centralises the "then" actions. Both
merge a built-in list with what plugins register, and both are served
to the editor so the frontend doesn't carry a hand-maintained copy
that drifts (the old frontend ACTION_SCHEMAS had already drifted from
the backend handler set).

A spec entry:
    {
        "kind":   "create_task",          # matches automation.then[].kind
        "label":  "Create a task",        # editor button text
        "group":  "Do work",              # optgroup header
        "scopes": ["cycle"],              # ["cycle"] | ["pupdate"] | both
        "description": "…",               # editor help text
        "fields": [ {name,type,label,…} ] # form-builder field schema
    }

The handler that actually runs a kind lives elsewhere: built-ins in
brain/automations/actions, plugin ones in plugin.action_handlers().
resolve_action() in that package builds the flat lookup. This module
is purely the spec/discovery side.
"""

_PRIORITY = [
    {"value": "low", "label": "low"},
    {"value": "normal", "label": "normal"},
    {"value": "high", "label": "high"},
    {"value": "urgent", "label": "urgent"},
]

_TASK_TYPES = [
    {"value": "todo", "label": "todo (generic)"},
    {"value": "bug", "label": "bug"},
    {"value": "feature", "label": "feature"},
    {"value": "coding", "label": "coding (you'll assign an agent later)"},
    {"value": "review", "label": "review (you owe someone a review)"},
]


# The canonical built-in action set. Kind strings here MUST match the
# keys in brain/automations/actions.ACTIONS.
BUILTIN_AUTOMATION_ACTIONS = [
    {
        "kind": "run_agent_job",
        "label": "Run an agent job (pack-owned)",
        "group": "Do work",
        "scopes": ["cycle"],
        "description": (
            "Spawn an agent to do a one-shot task: cartograph a repo, "
            "investigate an incident, run a scheduled skill. Pack-owned: "
            "lands on the Pack page, not the Tasks list."
        ),
        "fields": [
            {"name": "kind", "type": "select", "label": "Kind", "default": "cartograph", "optionsKey": "agent_job_kinds"},
            {"name": "ask_first", "type": "bool", "label": "Ask me before running", "help": "when on, the job waits for your approval; off runs it directly."},
            {"name": "title", "type": "string", "label": "Title", "placeholder": "Can template {service} etc."},
            {"name": "description", "type": "textarea", "label": "Description / input", "rows": 2, "help": "skill input / what the agent should focus on"},
            {"name": "scope_repo", "type": "string", "label": "Repo", "placeholder": "org/repo or {service}", "advanced": True, "datalist": "repos"},
            {"name": "specialty_id", "type": "select", "label": "Specialty", "optionsKey": "specialties", "advanced": True, "help": "Extra context layered onto the agent's role. Silently dropped if the resolved agent doesn't have it attached."},
            {"name": "priority", "type": "select", "label": "Priority", "default": "normal", "options": _PRIORITY, "advanced": True},
        ],
    },
    {
        "kind": "notify_me",
        "label": "Notify me",
        "group": "Let me know",
        "scopes": ["cycle", "pupdate"],
        "description": (
            "Drops a notification on the Home page. Use when you just "
            "want to be told something happened, no task or agent spawn. "
            "Dismissable."
        ),
        "fields": [
            {"name": "title", "type": "string", "label": "Title", "placeholder": "e.g. 'CI has been red for 30 min' or '{pupdate_title}'", "help": "Defaults to the triggering pupdate's title. Supports tokens like {pupdate_title}, {repo}."},
            {"name": "body", "type": "textarea", "label": "Body", "rows": 2, "help": "Optional extra detail. Markdown. Supports {pupdate_body}, {pupdate_url}, {repo}."},
            {"name": "priority", "type": "select", "label": "Priority", "default": "normal", "options": _PRIORITY, "advanced": True},
            {"name": "url", "type": "string", "label": "Click-through URL", "placeholder": "https:// or {pupdate_url}", "advanced": True},
        ],
    },
    {
        "kind": "create_task",
        "label": "Create a task (user-owed)",
        "group": "Do work",
        "scopes": ["cycle"],
        "description": (
            "Create a task you own: a todo / bug / feature that lives on "
            "the Tasks page. Use this when the work surfaces to you, not "
            "the pack."
        ),
        "fields": [
            {"name": "title", "type": "string", "label": "Title"},
            {"name": "type", "type": "select", "label": "Task type", "default": "todo", "options": _TASK_TYPES},
            {"name": "description", "type": "textarea", "label": "Description", "rows": 2},
            {"name": "auto_launch", "type": "bool", "label": "Launch an agent immediately", "help": "For review/investigation/cartograph/repo_analysis types: skip manual Assign and spawn a linked agent job. No-op on todo/bug/feature."},
            {"name": "repo", "type": "string", "label": "Repo", "placeholder": "org/repo", "advanced": True, "datalist": "repos"},
            {"name": "priority", "type": "select", "label": "Priority", "default": "normal", "options": _PRIORITY, "advanced": True},
        ],
    },
    {
        "kind": "dismiss_pupdate",
        "label": "Dismiss it (archive)",
        "group": "Handle the pupdate",
        "scopes": ["pupdate"],
        "description": "Archives the pupdate. Pure noise-reduction.",
        "fields": [],
    },
    {
        "kind": "create_task_from_pupdate",
        "label": "Create a task from it (user-owed)",
        "group": "Handle the pupdate",
        "scopes": ["pupdate"],
        "description": (
            "Uses the pupdate's title/priority as the task seed. Lands "
            "on the Tasks page as work you own."
        ),
        "fields": [
            {"name": "task_type", "type": "select", "label": "Task type", "default": "todo", "options": _TASK_TYPES},
            {"name": "task_priority", "type": "select", "label": "Task priority", "options": _PRIORITY, "advanced": True},
        ],
    },
    {
        "kind": "spawn_agent_job_from_pupdate",
        "label": "Spawn an agent job from it (pack-owned)",
        "group": "Handle the pupdate",
        "scopes": ["pupdate"],
        "description": (
            "Pack handles this pupdate, e.g. incident -> investigate. "
            "Job uses the pupdate's repo and title as context."
        ),
        "fields": [
            {"name": "kind", "type": "select", "label": "Job kind", "default": "investigation", "optionsKey": "agent_job_kinds"},
            {"name": "ask_first", "type": "bool", "label": "Ask me before running"},
            {"name": "title", "type": "string", "label": "Title override (optional)", "advanced": True},
            {"name": "description", "type": "textarea", "label": "Description override (optional)", "rows": 2, "advanced": True},
            {"name": "specialty_id", "type": "select", "label": "Specialty", "optionsKey": "specialties", "advanced": True, "help": "Extra context layered onto the agent's role. Silently dropped if the resolved agent doesn't have it attached."},
            {"name": "priority", "type": "select", "label": "Priority", "options": _PRIORITY, "advanced": True},
        ],
    },
    {
        "kind": "complete_linked_task",
        "label": "Close the linked task (PR merged / approved)",
        "group": "Handle the pupdate",
        "scopes": ["pupdate"],
        "description": (
            "Closes tasks whose url matches the pupdate's url. Cleans "
            "up worktrees for Maiko-owned coding tasks."
        ),
        "fields": [],
    },
    {
        "kind": "skip",
        "label": "Skip it (acknowledge, no action)",
        "group": "Handle the pupdate",
        "scopes": ["pupdate"],
        "description": (
            "Marks the pupdate processed without dispatching anything. "
            "Useful for 'ignore this pattern' without deleting the "
            "automation."
        ),
        "fields": [],
    },
]


def _prettify(kind):
    return kind.replace("_", " ").strip().capitalize()


def collect_all():
    """Built-in action specs merged with plugin-registered ones.

    De-duplicated by `kind`, first writer wins, so built-ins take
    precedence over a plugin trying to redeclare a core kind. A plugin
    with a broken register_actions() hook is skipped, not fatal.
    """
    from planet_maiko.plugins.loader import get_plugins

    seen = set()
    merged = []
    for spec in BUILTIN_AUTOMATION_ACTIONS:
        seen.add(spec["kind"])
        merged.append(spec)

    for plugin in get_plugins():
        try:
            entries = plugin.register_actions() or []
        except Exception:
            continue
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            kind = raw.get("kind")
            if not kind or kind in seen:
                continue
            seen.add(kind)
            merged.append({
                "kind": kind,
                "label": raw.get("label") or _prettify(kind),
                "group": raw.get("group") or (plugin.name or "Plugin"),
                "scopes": raw.get("scopes") or ["cycle"],
                "description": raw.get("description") or "",
                "fields": raw.get("fields") or [],
            })

    return merged
