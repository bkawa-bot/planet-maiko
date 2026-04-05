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
                    "tournaments", "heartbeats", "projects", "scheduled_skills")
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
