"""Brain cycle - the clock tick that drives all processors.

Each cycle runs all registered processors in order,
just like a CPU executes its pipeline on each clock tick.

Processor pipeline:
    1. agents:     process agent pupdates (auto-complete tasks)
    2. awareness:  detect conflicts between active agents (A2A)
    3. correlator: group related pupdates into incidents
    4. pupdates:   match pupdates against rules + triage
    5. learning:   aggregate signals into learnings
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Track cycle history for status reporting
_last_cycle = None
_cycle_count = 0


def run(app):
    """Execute one full brain cycle.

    Args:
        app: Flask app (needed for app context)

    Returns:
        dict with results from each processor
    """
    global _last_cycle, _cycle_count

    with app.app_context():
        logger.info(f"=== Brain cycle #{_cycle_count + 1} ===")

        results = {}

        # Phase 1: Process agent pupdates first (auto-complete tasks, etc.)
        from planet_maiko.agents.monitor import process_agent_pupdates
        results["agents"] = process_agent_pupdates()

        # Phase 2: Check for conflicts between active agents + attempt A2A resolution
        from planet_maiko.brain.awareness.conflicts import detect_conflicts, send_conflict_warnings, resolve_conflicts
        from planet_maiko.agents.coding_agent import list_prepared
        try:
            prepared = list_prepared()
            worktrees = [
                {"task_id": a.get("task_id", ""), "worktree_path": a.get("worktree_path", "")}
                for a in prepared if a.get("worktree_path")
            ]
            if len(worktrees) >= 2:
                conflicts = detect_conflicts(worktrees)
                if conflicts:
                    # Try A2A resolution first
                    resolution = resolve_conflicts(conflicts)
                    results["awareness"] = {
                        "conflicts": len(conflicts),
                        **resolution,
                    }
                else:
                    results["awareness"] = {"conflicts": 0, "resolved": 0, "escalated": 0}
            else:
                results["awareness"] = {"conflicts": 0, "resolved": 0, "escalated": 0}
        except Exception as e:
            logger.debug(f"Awareness check skipped: {e}")
            results["awareness"] = {"conflicts": 0, "warnings_sent": 0}

        # Phase 3: Correlate related pupdates into incidents
        from planet_maiko.brain.pupdates.correlator import correlate
        results["correlator"] = correlate()

        # Phase 3: Process remaining pupdates through rules + triage
        from planet_maiko.brain.pupdates.processor import process as process_pupdates
        results["pupdates"] = process_pupdates()

        # Phase 4: Aggregate feedback signals into learnings
        from planet_maiko.brain.learning.processor import process_signals
        results["learning"] = process_signals()

        _last_cycle = datetime.now(timezone.utc)
        _cycle_count += 1

        logger.info(f"=== Cycle #{_cycle_count} complete ===")
        return results


_status_cache = None
_status_cache_at = 0


def get_status():
    """Get brain status for the dashboard. Cached for 5 seconds to reduce DB load."""
    import time
    global _status_cache, _status_cache_at
    if _status_cache and (time.time() - _status_cache_at) < 5:
        return _status_cache
    """Get brain status for the dashboard."""
    return {
        "last_cycle": _last_cycle.isoformat() if _last_cycle else None,
        "cycle_count": _cycle_count,
    }
