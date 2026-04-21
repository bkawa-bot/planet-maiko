"""Base class for Planet Maiko plugins.

All methods are optional — only override what you need.

Example plugin (~/.maiko/plugins/my_plugin.py):

    from planet_maiko.plugins.base import MaikoPlugin

    class MyPlugin(MaikoPlugin):
        name = "my-plugin"

        def on_startup(self, app):
            print("Plugin loaded!")

        def on_brain_cycle(self, phase, results, app):
            if phase == "learning":
                print(f"Learnings processed: {results}")
"""


class MaikoPlugin:
    """Base class for all Planet Maiko plugins."""

    name = ""

    def on_startup(self, app):
        """Called once during app creation.

        Use this to register Flask blueprints, create DB tables,
        or set up any state your plugin needs.

        Args:
            app: the Flask application instance
        """

    def on_brain_cycle(self, phase, results, app):
        """Called after each brain cycle phase completes.

        Args:
            phase: name of the phase that just ran
                   ("agents", "awareness", "correlator", "pupdates",
                    "llm_triage", "learning", "classification",
                    "heartbeats", "projects", "scheduled_skills")
            results: dict of results from this phase
            app: Flask app (for app context if needed)
        """

    def on_pupdate_created(self, pupdate):
        """Called when a new pupdate is inserted into the database.

        Args:
            pupdate: the Pupdate model instance
        """

    def on_task_created(self, task):
        """Called when a new task is created.

        Args:
            task: the Task model instance
        """

    def register_commands(self, subparsers):
        """Register CLI subcommands.

        Args:
            subparsers: argparse subparsers object from the main CLI.
                        Call subparsers.add_parser("my-cmd", ...) to add commands.
        """

    def get_config_defaults(self):
        """Return default config values for this plugin.

        Returns:
            dict that gets merged into the config under the plugin's name.
            e.g. {"my_plugin": {"api_url": "https://..."}}
        """
        return {}

    def register_pupdate_types(self):
        """Declare pupdate types this plugin emits.

        Entries surface in the Automation editor's type dropdown so users
        can build "when / then" rules that fire on plugin events. Return
        an empty list (default) to stay invisible.

        Each entry:
            {
                "name":  "jira_issue_created",       # required — the type value
                "label": "Jira issue created",       # optional — shown in the UI
                "group": "Jira",                     # optional — optgroup header
            }

        The `name` is what your poller writes into `Pupdate.type`. The
        `label` defaults to a prettified version of `name`, and `group`
        defaults to the plugin's `name` attribute.
        """
        return []

    def register_default_automations(self):
        """Seed starter automations for this plugin.

        Installed on startup the first time the plugin loads and marked
        with `created_by="plugin:<plugin-name>"` so they're visible and
        editable from the Automations page. Returning the same entry
        again is a no-op — re-seed is gated on (created_by + seed_key).

        Each entry mirrors the Automation row's JSON shape:
            {
                "seed_key":    "on_jira_assigned",   # stable id within plugin
                "name":        "Triage new Jira assignments",
                "description": "…",
                "when":  [{"kind": "pupdate_match", "config": {...}}],
                "then":  [{"kind": "create_task", "config": {...}}],
                "when_logic": "all",                 # optional, default "all"
                "execution_scope": "pupdate",        # optional, default "cycle"
                "cooldown_days": 7,                  # optional, default 7
            }

        `seed_key` is what distinguishes two automations from the same
        plugin — pick something stable so you don't duplicate when the
        user renames the row.
        """
        return []

    def get_config_schema(self):
        """Describe configurable fields so Settings can render a form for them.

        When set, Settings → Integrations renders one input per field
        and writes the user-entered values into the same `config.<name>.<field>`
        slot that `get_config_defaults()` populated. Returning an empty
        dict keeps the existing behavior (plugin appears in Settings
        with just an on/off toggle).

        Supported field shapes (any unspecified keys are ignored):
          {
            "<field_name>": {
              "type": "string" | "bool" | "number" | "list",
              "label": "Human-facing label",       # optional, defaults to field_name
              "help":  "One-sentence explanation", # optional
              "secret": True,                      # optional, masks the input
              "placeholder": "org/repo",           # optional hint for string fields
            },
            ...
          }

        Returns:
            dict keyed by field name as above. Default: empty (no form shown).
        """
        return {}
