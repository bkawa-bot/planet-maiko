"""Base class for Planet Maiko plugins.

All hook methods are optional, only override what you need:

  - on_startup(app)            one-time setup (blueprints, tables)
  - on_cycle_tick(app)         fires once per brain cycle. The right
                               hook for periodic background work (poll
                               an API, sync, cleanup).
  - on_brain_cycle(phase, ...) fires once per cycle PHASE. Use only
                               when you care about a specific phase's
                               results.
  - on_pupdate_created / on_task_created   react to a single event.

Plugins that surface external data call self.emit_pupdates(...). The
scheduled-fetch shape (poll on an interval) is the PollerPlugin
subclass in plugins/poller.py.

Example plugin (~/.maiko/plugins/my_plugin.py):

    from planet_maiko.plugins.base import MaikoPlugin

    class MyPlugin(MaikoPlugin):
        name = "my-plugin"

        def on_startup(self, app):
            print("Plugin loaded!")
"""

import hashlib
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _pupdate_id(plugin_name, source_id):
    """Deterministic pupdate id from (plugin, source_id). Stable across
    polls so the same source_id never double-inserts.
    """
    raw = f"{plugin_name}:{source_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


class MaikoPlugin:
    """Base class for all Planet Maiko plugins."""

    name = ""

    #: Top-level config key this plugin's settings live under. Defaults
    #: to `name`. is_enabled() reads `config[config_key].enabled`.
    config_key = None

    def is_enabled(self):
        """True unless the user turned this plugin off in Settings.

        Reads `config[config_key or name].enabled`. Plugins with no
        config section (no enabled flag) default to enabled — a plugin
        that's loaded but unconfigured still gets its hooks. fire_hook()
        skips plugins where this returns False, so a disabled plugin is
        fully inert until re-enabled (which needs a restart, same as
        the loader's disabled list).
        """
        try:
            from planet_maiko.config import load_config
            key = self.config_key or self.name
            section = load_config().get(key)
            if not isinstance(section, dict) or "enabled" not in section:
                return True
            return bool(section.get("enabled"))
        except Exception:
            return True

    def emit_pupdates(self, pupdate_dicts, signal_dicts=None, db_session=None):
        """Dedup + insert pupdates (and optional signals) for this plugin.

        Args:
            pupdate_dicts: list of dicts. Each must have source_id, type,
                title. Optional: priority, body, url, actionable,
                action_hint, tags, metadata, expires_at.
            signal_dicts: optional list for the learning system. Each
                should have category, text, source_type; severity, repo,
                language, file_path optional.
            db_session: SQLAlchemy session. Defaults to the ambient
                Flask-SQLAlchemy session.

        Returns:
            int: number of new pupdate rows created. Rows whose
            source_id already exists are skipped.
        """
        from planet_maiko.models.pupdate import Pupdate

        if db_session is None:
            from planet_maiko.database import db
            db_session = db.session

        created = 0
        for pd in pupdate_dicts or []:
            pup_id = _pupdate_id(self.name, pd["source_id"])
            if db_session.get(Pupdate, pup_id) is not None:
                continue
            pupdate = Pupdate(
                id=pup_id,
                timestamp=datetime.now(timezone.utc),
                source=self.name,
                source_id=pd["source_id"],
                type=pd["type"],
                priority=pd.get("priority", "normal"),
                title=pd["title"],
                body=pd.get("body"),
                url=pd.get("url"),
                actionable=pd.get("actionable", False),
                action_hint=pd.get("action_hint"),
                tags=pd.get("tags", []),
                extra=pd.get("metadata", {}),
            )
            if pd.get("expires_at"):
                pupdate.expires_at = datetime.fromisoformat(pd["expires_at"])
            db_session.add(pupdate)
            created += 1

        signal_count = 0
        if signal_dicts:
            from planet_maiko.models.signal import Signal
            for s in signal_dicts:
                db_session.add(Signal(
                    category=s.get("category", "domain_knowledge"),
                    text=s["text"][:500],
                    source_type=s.get("source_type", self.name),
                    reviewer=s.get("reviewer"),
                    severity=s.get("severity", "suggestion"),
                    repo=s.get("repo"),
                    language=s.get("language"),
                    file_path=s.get("file_path"),
                    synthesized=True,
                ))
                signal_count += 1

        if created or signal_count:
            db_session.commit()
            if created:
                logger.info(f"[{self.name}] {created} new pupdate(s)")
            if signal_count:
                logger.info(f"[{self.name}] {signal_count} learning signal(s)")

        return created

    def on_cycle_tick(self, app):
        """Called exactly once per brain cycle.

        The right hook for periodic background work that doesn't care
        about phases: polling an external service, syncing, cleanup.
        PollerPlugin implements this. Default is a no-op.
        """

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

    def register_actions(self):
        """Declare automation actions this plugin handles.

        Symmetric with register_pupdate_types: that feeds the Automation
        editor's "when" dropdown, this feeds the "then" dropdown. Each
        entry both advertises the action to the editor and tells the
        form-builder what config fields to render.

        Each entry:
            {
                "kind":  "jira_transition",          # required, unique
                "label": "Move the Jira issue",      # optional
                "group": "Jira",                     # optional optgroup
                "description": "…",                  # optional help text
                "scopes": ["pupdate"],               # ["cycle"] | ["pupdate"]
                                                     #   | both. Default ["cycle"].
                "fields": [                          # optional; form-builder
                    {"name": "state", "type": "string",
                     "label": "Target state"},
                ],
            }

        `kind` is what lands in `automation.then[].kind`. The engine
        dispatches it via action_handlers(). Field dicts use the same
        shape as the built-in ACTION_SCHEMAS the editor already renders
        (name, type, label, default, options, advanced, help, …).
        """
        return []

    def action_handlers(self):
        """Map automation action `kind` -> handler callable.

        Returns {kind: fn} where fn has the same signature as the
        built-in _act_* handlers:

            fn(automation, config, *, pupdate, context)
              -> None | dict | {"error": str}

        Every kind declared in register_actions() should have an entry
        here. The automation engine builds one flat lookup at resolve
        time: built-in ACTIONS first, then plugin maps, so a plugin
        can't shadow a core action kind.
        """
        return {}

    def get_setup_actions(self):
        """Declare user-triggered setup actions, shown as buttons in
        Settings under this plugin's section.

        Distinct from automation actions: these are run by a human
        clicking a button (backfill history, import existing data,
        auto-configure), not by an automation rule firing.

        Each entry:
            {
                "key":   "import_issues",            # required, unique per plugin
                "label": "Import from Linear",       # required, button text
                "description": "Pull assigned issues + led projects",
                "destructive": False,                # optional, confirm-first
                "sync": False,                       # optional, see below
            }

        The button calls POST /api/plugins/<name>/actions/<key>.

        With sync=False (default) the work runs in a background thread
        and drops a completion memo. Right for slow backfills/imports.

        With sync=True the action runs inline and its return value is
        handed straight back to the form. Right for fast, interactive
        actions (test a connection, discover repos, fetch a picker's
        options) where the user is waiting on the result.
        """
        return []

    def run_setup_action(self, key):
        """Execute the setup action identified by `key`.

        Async actions (sync=False) run in a daemon thread inside an app
        context; the returned dict is summarized into the completion
        memo. Raising is fine, the failure is reported in the memo.

        Sync actions (sync=True) run inline; return a dict the Settings
        form consumes directly:
            {
              "ok": True,                       # optional, default True
              "message": "Connected as octocat",# shown next to the button
              "config_patch": {"repos": [...]}, # optional; merged into
                                                #   this plugin's config
                                                #   section (field->value)
              "options": {"team_id": [          # optional; populates a
                {"value": "id", "label": "..."} #   select field whose
              ]},                               #   options_from names
            }                                   #   this action's key
        A raise from a sync action is caught and shown as {ok: false}.

        Default raises for an unknown key so a typo in
        get_setup_actions() is loud.
        """
        raise NotImplementedError(
            f"{self.name}: no run_setup_action handler for {key!r}"
        )

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
              "type": "string" | "bool" | "number" | "list" | "select",
              "label": "Human-facing label",       # optional, defaults to field_name
              "help":  "One-sentence explanation", # optional
              "secret": True,                      # optional, masks the input
              "placeholder": "org/repo",           # optional hint for string fields
              "options": [                         # select: static choices
                {"value": "a", "label": "Option A"},
              ],
              "options_from": "fetch_teams",       # select: choices come
                                                   #   from the sync setup
                                                   #   action with this key
                                                   #   (its `options` map)
            },
            ...
          }

        A `select` with `options_from` renders an empty picker until the
        user runs that setup action; the saved value still shows as the
        raw value with a hint, so a configured field survives a reload.

        Returns:
            dict keyed by field name as above. Default: empty (no form shown).
        """
        return {}
