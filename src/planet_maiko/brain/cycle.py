"""Brain cycle - the clock tick that drives all processors.

Each cycle runs all phases in order, just like a CPU executes its
pipeline on each clock tick. Each phase is its own function so failures
are isolated and the orchestrator stays readable.

This file is now just registration + the run() loop. Every phase lives
under brain/phases/, grouped by topic:

  - phases/ingest.py       agents, auto_complete_reviews, awareness,
                           automations, pupdates
  - phases/synthesis.py    synthesis, learning
  - phases/health.py       stuck_check, stuck_escalation
  - phases/orchestrate.py  projects, orchestrate, unblock
  - phases/spawn_jobs.py   spawn_jobs_for_tasks (heavier, its own file)
  - phases/execute_jobs.py execute_agent_jobs
  - phases/execute_tasks.py execute_agent_tasks

Pipeline order is the _PHASES list below.
"""

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Track cycle history for status reporting
_last_cycle = None
_cycle_count = 0

_status_cache = None
_status_cache_at = 0


from .phases.ingest import (  # noqa: E402
    _phase_agents,
    _phase_auto_complete_reviews,
    _phase_awareness,
    _phase_automations,
    _phase_pupdates,
)
from .phases.synthesis import (  # noqa: E402
    _phase_synthesis,
    _phase_learning,
    _phase_insights_reconcile,
    _phase_rules_decay,
)
from .phases.health import (  # noqa: E402
    _phase_nudge_quiet_agents,
    _phase_stuck_check,
    _phase_stuck_escalation,
    _phase_worktree_sweep,
)
from .phases.orchestrate import (  # noqa: E402
    _phase_projects,
    _phase_orchestrate,
    _phase_unblock_tasks,
)
from .phases.spawn_jobs import _phase_spawn_jobs_for_tasks  # noqa: E402
from .phases.execute_jobs import _phase_execute_agent_jobs  # noqa: E402
from .phases.advance_workflows import _phase_advance_workflows  # noqa: E402


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# Phase order. Each entry is (result_key, phase_function). The orchestrator
# runs them in order, stores each result under its key, and fires plugin
# hooks for every phase.
_PHASES = [
    ("agents", _phase_agents),
    ("auto_complete_reviews", _phase_auto_complete_reviews),
    ("awareness", _phase_awareness),
    ("automations", _phase_automations),
    ("pupdates", _phase_pupdates),
    ("synthesis", _phase_synthesis),
    ("learning", _phase_learning),
    ("insights_reconcile", _phase_insights_reconcile),
    ("rules_decay", _phase_rules_decay),
    # Order matters: nudge first so a quiet agent gets a chance to
    # re-engage and stamp last_active_at before stuck_check would
    # flag it at the 15m mark.
    ("nudge_quiet_agents", _phase_nudge_quiet_agents),
    ("stuck_check", _phase_stuck_check),
    ("projects", _phase_projects),
    ("orchestrate", _phase_orchestrate),
    ("unblock", _phase_unblock_tasks),
    ("spawn_jobs_for_tasks", _phase_spawn_jobs_for_tasks),
    # Advance running flows: queue the next ready node(s). Sits just
    # before execute so a freshly-queued node job runs the same tick.
    ("advance_workflows", _phase_advance_workflows),
    ("execute_agent_jobs", _phase_execute_agent_jobs),
    ("stuck_escalation", _phase_stuck_escalation),
    # Daily-cadenced; the phase itself gates on a 24h cooldown so this
    # is cheap to invoke every cycle.
    ("worktree_sweep", _phase_worktree_sweep),
]


def run(app):
    """Execute one full brain cycle.

    Args:
        app: Flask app (needed for app context)

    Returns:
        dict mapping phase name -> result dict
    """
    global _last_cycle, _cycle_count

    from planet_maiko.database import db

    with app.app_context():
        logger.info(f"=== Brain cycle #{_cycle_count + 1} ===")

        results = {}
        for key, phase_fn in _PHASES:
            results[key] = phase_fn()
            # Clean the session after every phase. Phases own their
            # own commits; anything still pending here is a leak from
            # a phase that errored mid-write. Without this rollback,
            # the next phase's first query autoflushes the leaked
            # pending row and trips "UNIQUE constraint failed" /
            # similar errors that have nothing to do with the phase
            # the warning surfaces in (the user kept seeing
            # "stuck escalation skipped: ... pupdates.id" from a leak
            # in an earlier phase).
            try:
                db.session.rollback()
            except Exception as e:
                logger.warning(f"[cycle] post-phase rollback ({key}) skipped: {e}")

        # Fire plugin hooks. on_brain_cycle is per-phase (for plugins
        # that care about a specific phase's output); on_cycle_tick
        # fires exactly once per cycle (periodic work — pollers, sync,
        # cleanup). fire_hook skips disabled plugins.
        from planet_maiko.plugins.loader import fire_hook
        for phase_name, phase_results in results.items():
            fire_hook("on_brain_cycle", phase_name, phase_results, app)
        fire_hook("on_cycle_tick", app)

        _last_cycle = datetime.now(timezone.utc)
        _cycle_count += 1

        logger.info(f"=== Cycle #{_cycle_count} complete ===")
        return results


_WORKFLOW_PHASES = [
    ("advance_workflows", _phase_advance_workflows),
    ("execute_agent_jobs", _phase_execute_agent_jobs),
]


def run_workflow_tick(app):
    """Run only the workflow-driving phases (advance + execute), for the
    fast ticker to call between full brain cycles so an in-flight flow
    advances in near-real-time instead of once per cycle_interval. Runs on
    the same thread as the full cycle, so there's no concurrent agent-job
    pickup; per-phase rollback mirrors run()."""
    from planet_maiko.database import db
    with app.app_context():
        for key, phase_fn in _WORKFLOW_PHASES:
            try:
                phase_fn()
            except Exception as e:
                logger.warning(f"[workflow-tick] {key} skipped: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass


def get_status():
    """Get brain status for the dashboard. Cached for 5 seconds."""
    global _status_cache, _status_cache_at
    if _status_cache and (time.time() - _status_cache_at) < 5:
        return _status_cache

    pending = {}
    try:
        from planet_maiko.models.pupdate import Pupdate
        from planet_maiko.models.signal import Signal
        from planet_maiko.models.learning import Learning
        pending["unprocessed_pupdates"] = Pupdate.query.filter_by(brain_processed=False, dismissed=False).count()
        pending["unclassified_signals"] = Signal.query.filter_by(category="pattern", aggregated=False).count()
        pending["pending_learnings"] = Learning.query.filter_by(status="pending").count()
    except Exception as e:
        logger.debug(f"[cycle] Status pending counts failed: {e}")

    _status_cache = {
        "last_cycle": _last_cycle.isoformat() if _last_cycle else None,
        "cycle_count": _cycle_count,
        "pending": pending,
    }
    _status_cache_at = time.time()
    return _status_cache
