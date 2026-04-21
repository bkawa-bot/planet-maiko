"""Pupdate-type registry.

Centralises the list of types the core pollers emit. Plugins extend it
via the `register_pupdate_types()` hook — the combined list is served
by /api/pupdate-types and drives the Automation editor's type dropdown.

A pupdate `type` is just a string — the DB doesn't validate against
this list. It exists so the UI can offer autocomplete / optgroups
without scraping the table (which would surface typos and one-off
types). Grouped by source so the dropdown renders with optgroups.
"""


# (name, label, group)
#
# Only types that a default poller or Maiko core actually emits live
# here. Ops-y types (incident, error_spike, deploy_*, batch_job_*)
# were removed — nothing in the default install produces them, so
# advertising them in the Automation dropdown led to user-created
# rules that could never fire. Plugins that add those signal sources
# register their own types via `register_pupdate_types()` and they
# show up in the dropdown alongside these.
BUILTIN_PUPDATE_TYPES = [
    # GitHub poller
    ("pr_review_requested",     "PR review requested",      "GitHub"),
    ("pr_changes_requested",    "PR changes requested",     "GitHub"),
    ("pr_approved",             "PR approved",              "GitHub"),
    ("pr_merged",               "PR merged",                "GitHub"),
    ("pr_ci_passed",            "PR CI passed",             "GitHub"),
    ("pr_ci_failed",            "PR CI failed",             "GitHub"),
    ("pr_review_commented",     "PR review comment",        "GitHub"),
    # Linear poller
    ("linear_assigned",         "Linear issue assigned",    "Linear"),
    ("linear_mention",          "Linear mention",           "Linear"),
    ("linear_status_changed",   "Linear status changed",    "Linear"),
    # Calendar poller
    ("calendar_event",          "Calendar event",           "Calendar"),
    ("calendar_1on1",           "Calendar 1:1",             "Calendar"),
    # Agents (emitted by Maiko core — the agents themselves)
    ("agent_ready_for_review",  "Agent ready for review",   "Agents"),
    ("agent_plan_for_approval", "Agent plan ready",         "Agents"),
    ("agent_stuck",             "Agent stuck",              "Agents"),
    ("agent_proposal",          "Agent proposal",           "Agents"),
]


def builtin_entries():
    """Return built-in types as a list of {name, label, group} dicts."""
    return [
        {"name": n, "label": label, "group": group}
        for (n, label, group) in BUILTIN_PUPDATE_TYPES
    ]


def collect_all():
    """Merge built-in types with anything plugins register.

    Plugin entries may omit `label` (falls back to the name) or `group`
    (falls back to the plugin's own name). De-duplicated by `name` —
    first writer wins, so built-ins take precedence over a plugin that
    tries to redeclare an existing type.
    """
    from planet_maiko.plugins.loader import get_plugins

    seen = set()
    merged = []
    for entry in builtin_entries():
        seen.add(entry["name"])
        merged.append(entry)

    for plugin in get_plugins():
        try:
            entries = plugin.register_pupdate_types() or []
        except Exception:
            # A plugin with a broken hook shouldn't poison the dropdown.
            continue
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            merged.append({
                "name": name,
                "label": raw.get("label") or _prettify(name),
                "group": raw.get("group") or (plugin.name or "Plugin"),
            })

    return merged


def _prettify(name):
    """Convert snake_case identifiers into a readable label."""
    return name.replace("_", " ").strip().capitalize()
