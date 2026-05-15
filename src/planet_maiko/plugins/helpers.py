"""Utilities for plugins that emit pupdates and signals.

Plugin authors who want the "fetch external data, dedup, insert pupdate
rows" shape call emit_pupdates(), or subclass PollerPlugin and override
poll() / to_pupdates() / to_signals() / _after_sync().
"""

import hashlib
import logging
import time
from datetime import datetime, timezone

from planet_maiko.plugins.base import MaikoPlugin

logger = logging.getLogger(__name__)


def _pupdate_id(plugin_name, source_id):
    raw = f"{plugin_name}:{source_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def emit_pupdates(plugin_name, pupdate_dicts, signal_dicts=None, db_session=None):
    """Dedup + insert pupdates (and optional signals) for a plugin.

    Args:
        plugin_name: source name for the rows (e.g. "github").
        pupdate_dicts: list of dicts. Each must have source_id, type, title.
            Optional: priority, body, url, actionable, action_hint, tags,
            metadata, expires_at.
        signal_dicts: optional list of dicts for the learning system. Each
            should have category, text, source_type; severity, repo, etc.
            optional.
        db_session: SQLAlchemy session. Defaults to the Flask-SQLAlchemy
            ambient session.

    Returns:
        int: number of new pupdate rows created (rows whose source_id
        already existed are skipped).
    """
    from planet_maiko.models.pupdate import Pupdate

    if db_session is None:
        from planet_maiko.database import db
        db_session = db.session

    created = 0
    for pd in pupdate_dicts or []:
        pup_id = _pupdate_id(plugin_name, pd["source_id"])
        if db_session.get(Pupdate, pup_id) is not None:
            continue
        pupdate = Pupdate(
            id=pup_id,
            timestamp=datetime.now(timezone.utc),
            source=plugin_name,
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
                source_type=s.get("source_type", plugin_name),
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
            logger.info(f"[{plugin_name}] {created} new pupdate(s)")
        if signal_count:
            logger.info(f"[{plugin_name}] {signal_count} learning signal(s)")

    return created


class PollerPlugin(MaikoPlugin):
    """MaikoPlugin shape for "fetch external data on a schedule" integrations.

    Subclasses override:
        poll(config)            fetch raw data from the source.
        to_pupdates(raw)        convert raw data to pupdate dicts.
        to_signals(raw)         (optional) emit learning signals.
        _after_sync(raw, db)    (optional) post-commit per-poller work.

    Required class attrs:
        name                    unique plugin name (e.g. "github").

    Optional class attrs:
        poll_phase              brain-cycle phase to fire on (default "agents").
        config_key              top-level config key (defaults to name).

    The base handles config lookup, the "enabled" gate, the interval
    check (poll_interval_minutes in config), exception isolation, and
    invoking emit_pupdates + _after_sync. Subclasses just describe what
    to fetch and how to shape it.
    """

    poll_phase = "agents"
    config_key = None  # falls back to self.name

    def __init__(self):
        self._last_polled = 0.0

    def _get_config(self):
        from planet_maiko.config import load_config
        key = self.config_key or self.name
        return load_config().get(key, {}) or {}

    def on_brain_cycle(self, phase, results, app):
        if phase != self.poll_phase:
            return
        config = self._get_config()
        if not config.get("enabled"):
            return
        interval = float(config.get("poll_interval_minutes", 5)) * 60
        now = time.time()
        if now - self._last_polled < interval:
            return
        self._last_polled = now
        self._poll_now(config)

    def _poll_now(self, config=None):
        """Run the full poll -> emit pipeline once. Skips the interval
        gate; callers (typically on_brain_cycle, or a manual force_poll
        from the overview prepoll) are responsible for whether the poll
        should fire.
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
        emit_pupdates(self.name, pupdate_dicts, signal_dicts, db_session=db.session)

        try:
            self._after_sync(raw, db.session)
        except Exception as e:
            logger.warning(f"[{self.name}] _after_sync failed: {e}")

    def force_poll(self, app):
        """Run a poll inside an app context, ignoring the interval gate.

        Used by the overview prepoll so a regen sees fresh data without
        waiting for the next brain-cycle tick. Still honors the 'enabled'
        config flag so a disabled plugin doesn't surprise-poll.
        """
        config = self._get_config()
        if not config.get("enabled"):
            return
        with app.app_context():
            self._last_polled = time.time()
            self._poll_now(config)

    # ----- subclass overrides -----

    def poll(self, config):
        raise NotImplementedError

    def to_pupdates(self, raw_data):
        raise NotImplementedError

    def to_signals(self, raw_data):
        return []

    def _after_sync(self, raw_data, db_session):
        return None
