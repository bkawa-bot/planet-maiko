import logging
import threading
import time
from importlib.metadata import entry_points

from planet_maiko.config import load_config
from planet_maiko.database import db

logger = logging.getLogger(__name__)


def _discover_pollers():
    """Discover all registered pollers via entry_points."""
    pollers = {}
    eps = entry_points(group="planet_maiko.pollers")
    for ep in eps:
        try:
            poller_cls = ep.load()
            pollers[ep.name] = poller_cls()
        except Exception as e:
            logger.warning(f"Failed to load poller '{ep.name}': {e}")
    logger.info(f"[scheduler] Discovered pollers: {list(pollers.keys())}")
    return pollers


# Lazy-loaded poller registry
POLLERS = None


def _get_pollers():
    global POLLERS
    if POLLERS is None:
        POLLERS = _discover_pollers()
    return POLLERS


class PollerScheduler:
    """Runs pollers on their configured intervals in background threads."""

    def __init__(self, app):
        self.app = app
        self._threads = {}
        self._stop_event = threading.Event()

    def start(self):
        """Start all enabled pollers."""
        config = load_config()
        for name, poller in _get_pollers().items():
            integration_config = config.get(name, {})
            if not integration_config.get("enabled", False):
                logger.info(f"[scheduler] {name} is disabled, skipping")
                continue

            interval = integration_config.get("poll_interval_minutes", 5) * 60
            thread = threading.Thread(
                target=self._poll_loop,
                args=(name, poller, integration_config, interval),
                daemon=True,
                name=f"poller-{name}",
            )
            self._threads[name] = thread
            thread.start()
            logger.info(f"[scheduler] Started {name} poller (every {interval}s)")

    def stop(self):
        """Signal all poller threads to stop."""
        self._stop_event.set()

    def _poll_loop(self, name, poller, config, interval):
        """Run a single poller on a loop."""
        # Small initial delay to let the app fully start
        time.sleep(2)

        while not self._stop_event.is_set():
            try:
                with self.app.app_context():
                    created = poller.run(config, db.session)
                    if created:
                        logger.info(f"[scheduler] {name}: {created} new pupdate(s)")
                        # Run brain cycle after new pupdates arrive
                        from brain.cycle import run as brain_cycle
                        brain_cycle(self.app)
            except Exception as e:
                logger.error(f"[scheduler] {name} poll error: {e}")

            # Wait for the interval, but check stop_event periodically
            for _ in range(int(interval)):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def run_once(self, poller_name):
        """Run a specific poller once (useful for testing / manual trigger)."""
        poller = _get_pollers().get(poller_name)
        if not poller:
            raise ValueError(f"Unknown poller: {poller_name}")

        config = load_config().get(poller_name, {})
        with self.app.app_context():
            return poller.run(config, db.session)

    def get_status(self):
        """Get status of all pollers."""
        config = load_config()
        status = {}
        for name in _get_pollers():
            integration_config = config.get(name, {})
            thread = self._threads.get(name)
            status[name] = {
                "enabled": integration_config.get("enabled", False),
                "running": thread.is_alive() if thread else False,
                "interval_minutes": integration_config.get("poll_interval_minutes", 5),
            }
        return status
