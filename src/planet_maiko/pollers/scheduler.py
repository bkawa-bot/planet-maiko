import logging
import threading
import time
from datetime import datetime, timezone
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
        # Lightweight per-poller status for the system-health strip.
        # Each entry: {last_run_at, last_success_at, last_error,
        # last_created_count, interval_seconds}. Memory-only — we don't
        # need cross-restart history for "is anything broken right now".
        self.poller_status = {}
        # Most-recent brain cycle timestamp (set inside _brain_cycle_loop).
        self.last_brain_cycle = None

    def start(self):
        """Start all enabled pollers, scheduled skills, and script pollers."""
        config = load_config()

        # Start plugin pollers
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

        # Start the periodic brain cycle. This was previously
        # event-driven only — the cycle ran whenever a poller created a
        # pupdate. Without enabled pollers (or during quiet periods)
        # the brain effectively stopped ticking, so phases like
        # synthesis / clustering / scheduled skills stalled until the
        # user manually hit POST /brain/cycle. Now there's a steady
        # heartbeat regardless of poller activity.
        brain_interval = config.get("brain", {}).get("cycle_interval_minutes", 5) * 60
        brain_thread = threading.Thread(
            target=self._brain_cycle_loop,
            args=(brain_interval,),
            daemon=True,
            name="brain-cycle",
        )
        self._threads["brain_cycle"] = brain_thread
        brain_thread.start()
        logger.info(f"[scheduler] Started brain cycle (every {brain_interval}s)")

        # Scheduled skills moved to the Automation engine — any
        # CustomSkill with schedule_interval_minutes now runs via a
        # seeded Automation (cadence + run_skill) evaluated by the
        # brain cycle's automations phase.

        # Start script pollers
        self._start_script_pollers(config)

        # Nightly DB backups. Small insurance — one corrupted shutdown
        # otherwise loses every task, learning, insight, and agent
        # profile the user has built up.
        from planet_maiko.backups import run_daily_loop as _backup_loop
        backup_thread = threading.Thread(
            target=_backup_loop,
            args=(self._stop_event,),
            daemon=True,
            name="backup-loop",
        )
        self._threads["backups"] = backup_thread
        backup_thread.start()
        logger.info("[scheduler] Started backup loop (daily)")

    def stop(self):
        """Signal all poller threads to stop."""
        self._stop_event.set()

    def _poll_loop(self, name, poller, config, interval):
        """Run a single poller on a loop."""
        # Small initial delay to let the app fully start
        time.sleep(2)

        # Seed the status entry so /system/health shows the poller even
        # before its first tick completes.
        self.poller_status[name] = {
            "last_run_at": None,
            "last_success_at": None,
            "last_error": None,
            "last_created_count": 0,
            "interval_seconds": int(interval),
        }

        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc).isoformat()
            try:
                with self.app.app_context():
                    created = poller.run(config, db.session)
                    if created:
                        logger.info(f"[scheduler] {name}: {created} new pupdate(s)")
                    self.poller_status[name].update({
                        "last_run_at": now,
                        "last_success_at": now,
                        "last_error": None,
                        "last_created_count": created or 0,
                    })

                    # Run brain cycle if there's new data OR unprocessed items
                    from planet_maiko.models.pupdate import Pupdate as SchedulerPupdate
                    unprocessed = SchedulerPupdate.query.filter_by(
                        brain_processed=False, dismissed=False
                    ).count()
                    if created or unprocessed > 0:
                        from planet_maiko.brain.cycle import run as brain_cycle
                        brain_cycle(self.app)
            except Exception as e:
                logger.error(f"[scheduler] {name} poll error: {e}")
                self.poller_status[name].update({
                    "last_run_at": now,
                    "last_error": str(e)[:200],
                })

            # Wait for the interval, but check stop_event periodically
            for _ in range(int(interval)):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def _brain_cycle_loop(self, interval):
        """Tick the brain cycle on a fixed interval, independent of pollers."""
        # Stagger so we don't fire at literal startup before pollers
        # have had a chance to run their first poll.
        time.sleep(30)

        while not self._stop_event.is_set():
            try:
                with self.app.app_context():
                    from planet_maiko.brain.cycle import run as brain_cycle
                    brain_cycle(self.app)
                self.last_brain_cycle = datetime.now(timezone.utc).isoformat()
            except Exception as e:
                logger.error(f"[scheduler] brain cycle error: {e}")

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

    def _start_script_pollers(self, config):
        """Start threads for script-based pollers from config."""
        scripts = config.get("script_pollers", [])
        for script_cfg in scripts:
            if not script_cfg.get("enabled", True):
                continue
            name = script_cfg.get("name", "script")
            script_path = script_cfg.get("script", "")
            interval = script_cfg.get("interval_minutes", 30) * 60

            if not script_path:
                continue

            thread = threading.Thread(
                target=self._script_loop,
                args=(name, script_path, interval),
                daemon=True,
                name=f"script-{name}",
            )
            self._threads[f"script:{name}"] = thread
            thread.start()
            logger.info(f"[scheduler] Started script poller '{name}' (every {interval // 60}m)")

    def _script_loop(self, name, script_path, interval):
        """Run an external script on a loop."""
        import subprocess
        time.sleep(5)
        while not self._stop_event.is_set():
            try:
                result = subprocess.run(
                    [script_path], capture_output=True, text=True, timeout=120,
                )
                if result.returncode != 0:
                    logger.warning(f"[script:{name}] exited {result.returncode}: {result.stderr[:200]}")
                elif result.stdout.strip():
                    logger.info(f"[script:{name}] output: {result.stdout[:100]}")
            except Exception as e:
                logger.error(f"[script:{name}] error: {e}")
            for _ in range(int(interval)):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def get_status(self):
        """Get status of all pollers, scheduled skills, and script pollers."""
        config = load_config()
        status = {}
        for name in _get_pollers():
            integration_config = config.get(name, {})
            thread = self._threads.get(name)
            status[name] = {
                "type": "poller",
                "enabled": integration_config.get("enabled", False),
                "running": thread.is_alive() if thread else False,
                "interval_minutes": integration_config.get("poll_interval_minutes", 5),
            }

        # Include scheduled skills
        for key, thread in self._threads.items():
            if key.startswith("skill:"):
                skill_id = key.split(":", 1)[1]
                status[key] = {
                    "type": "scheduled_skill",
                    "running": thread.is_alive(),
                    "skill_id": skill_id,
                }

        # Include script pollers
        for key, thread in self._threads.items():
            if key.startswith("script:"):
                script_name = key.split(":", 1)[1]
                status[key] = {
                    "type": "script_poller",
                    "running": thread.is_alive(),
                    "name": script_name,
                }

        return status
