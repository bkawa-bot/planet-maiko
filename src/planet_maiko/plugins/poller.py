"""PollerPlugin: the scheduled-fetch shape of a Maiko plugin.

A poller is a MaikoPlugin whose on_cycle_tick runs a fetch pipeline
on an interval. This class owns exactly that lifecycle and nothing
else; the emit + enabled + hook surface all live on MaikoPlugin.

Subclass it, set `name`, implement poll() + to_pupdates():

    from planet_maiko.plugins.poller import PollerPlugin

    class JiraPlugin(PollerPlugin):
        name = "jira"

        def get_config_defaults(self):
            return {"jira": {"enabled": False, "poll_interval_minutes": 5}}

        def poll(self, config): ...
        def to_pupdates(self, raw): ...
"""

import logging
import time

from planet_maiko.plugins.base import MaikoPlugin

logger = logging.getLogger(__name__)


class PollerPlugin(MaikoPlugin):
    """Fetch external data on an interval and emit pupdates.

    Lifecycle (all handled here):
      - interval throttle via `poll_interval_minutes` config + the
        in-memory `_last_polled` timestamp.
      - the fetch pipeline: poll() -> to_pupdates() -> to_signals()
        -> emit_pupdates() -> _after_sync(), with per-stage exception
        isolation so one bad stage doesn't sink the others.
      - force_poll(): an interval-bypassing entry for the overview
        prepoll's on-demand refresh.

    The 'enabled' gate is NOT here. fire_hook() skips disabled plugins
    before on_cycle_tick is ever called (MaikoPlugin.is_enabled()).

    Subclass overrides:
      poll(config)            required. fetch raw data.
      to_pupdates(raw)        required. shape raw -> pupdate dicts.
      to_signals(raw)         optional. learning signals.
      _after_sync(raw, db)    optional. post-commit per-poller work.

    Optional class attr:
      config_key              top-level config key (defaults to name).
    """

    def __init__(self):
        self._last_polled = 0.0

    def _get_config(self):
        from planet_maiko.config import load_config
        key = self.config_key or self.name
        return load_config().get(key, {}) or {}

    def on_cycle_tick(self, app):
        config = self._get_config()
        interval = float(config.get("poll_interval_minutes", 5)) * 60
        now = time.time()
        if now - self._last_polled < interval:
            return
        self._last_polled = now
        self._poll_now(config)

    def _poll_now(self, config=None):
        """Run the full poll -> emit pipeline once. No interval gate;
        the caller (on_cycle_tick, or force_poll) decides whether to
        fire.
        """
        if config is None:
            config = self._get_config()

        try:
            raw = self.poll(config)
        except Exception as e:
            logger.error(f"[{self.name}] poll failed: {e}")
            return

        try:
            pupdate_dicts = self.to_pupdates(raw) or []
        except Exception as e:
            logger.error(f"[{self.name}] to_pupdates failed: {e}")
            pupdate_dicts = []

        signal_dicts = []
        try:
            signal_dicts = self.to_signals(raw) or []
        except Exception as e:
            logger.debug(f"[{self.name}] to_signals failed: {e}")

        from planet_maiko.database import db
        self.emit_pupdates(pupdate_dicts, signal_dicts, db_session=db.session)

        try:
            self._after_sync(raw, db.session)
        except Exception as e:
            logger.warning(f"[{self.name}] _after_sync failed: {e}")

    def force_poll(self, app):
        """Run a poll inside an app context, ignoring the interval gate.

        Used by the overview prepoll so a regen sees fresh data without
        waiting for the next brain-cycle tick. Honors the enabled flag
        so a disabled plugin doesn't surprise-poll.
        """
        if not self.is_enabled():
            return
        with app.app_context():
            self._last_polled = time.time()
            self._poll_now()

    # ----- subclass overrides -----

    def poll(self, config):
        raise NotImplementedError

    def to_pupdates(self, raw_data):
        raise NotImplementedError

    def to_signals(self, raw_data):
        return []

    def _after_sync(self, raw_data, db_session):
        return None
