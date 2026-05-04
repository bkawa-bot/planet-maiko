"""Action executors for the Automation engine.

Each `_act_*` function performs a side-effect and returns either
None (just did the work), or a dict to feed the next action's
context. The ACTIONS dict below is the dispatch table.

Split into:
  - cycle.py   — handlers that fire once per cycle (run_agent_job,
                 create_task, notify_me, skip)
  - pupdate.py — handlers that require a triggering pupdate
                 (spawn_agent_job_from_pupdate, dismiss_pupdate,
                 create_task_from_pupdate, complete_linked_task)
  - _helpers.py — _interpolate, _pupdate_snapshot,
                  format_pupdate_for_context (also re-used outside
                  the package for skill-prompt rendering)
"""

from ._helpers import (  # noqa: F401
    _interpolate,
    _pupdate_snapshot,
    format_pupdate_for_context,
)
from .cycle import (
    _act_run_agent_job,
    _act_create_task,
    _act_notify,
    _act_skip,
)
from .pupdate import (
    _act_spawn_agent_job_from_pupdate,
    _act_dismiss_pupdate,
    _act_create_task_from_pupdate,
    _act_complete_linked_task,
)


ACTIONS = {
    # Cycle-scope
    "run_agent_job": _act_run_agent_job,
    "create_task": _act_create_task,
    "notify_me": _act_notify,
    # Pupdate-scope (require context.pupdate to operate).
    "spawn_agent_job_from_pupdate": _act_spawn_agent_job_from_pupdate,
    "dismiss_pupdate": _act_dismiss_pupdate,
    "create_task_from_pupdate": _act_create_task_from_pupdate,
    "complete_linked_task": _act_complete_linked_task,
    "skip": _act_skip,
}
