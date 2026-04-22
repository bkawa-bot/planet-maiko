"""Bounded-concurrency helper for running LLM calls in parallel.

The learning pipeline (rule-gen, signal synthesis, etc.) makes dozens of
LLM calls per run. Serialized, that's 20-30 minutes of wall-clock on a
warm cache. This helper lets us fan out 2-4 calls at a time while
keeping DB writes on the main thread where SQLAlchemy's scoped session
is safe.

Contract for callers:
  - Jobs MUST be plain data (dicts, tuples, primitives). No ORM
    instances, no `db.session` references — those belong to the main
    thread's session and breaking that rule leads to DetachedInstance
    errors or silently-lost writes.
  - The runner runs in a worker thread. It should call the LLM and
    return plain data. It MUST NOT touch `db.session` or Flask app
    state.
  - The on_result callback fires on the main thread after each job
    completes. That's where DB writes and file appends belong.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 3


def run_parallel(jobs, runner, max_workers=DEFAULT_MAX_WORKERS,
                 on_result=None, log_prefix="llm-pool"):
    """Run `runner(job)` across jobs with bounded concurrency.

    Args:
        jobs: iterable of plain job inputs (dicts/tuples/primitives).
        runner: fn(job) -> result. Runs in a worker thread. If it
            raises, the exception is caught and passed to on_result
            as the `error` argument.
        max_workers: concurrent worker count. 3 is a reasonable default
            — more risks saturating the API or spawning too many claude
            subprocesses.
        on_result: optional fn(job, result, error) called on the MAIN
            thread after each worker completes, in completion order
            (not job order). Use this for DB writes and file appends.
        log_prefix: thread name prefix for debuggability.

    Returns:
        list of (job, result, error) tuples in completion order.
    """
    jobs = list(jobs)
    if not jobs:
        return []

    if max_workers < 1:
        max_workers = 1

    results = []
    with ThreadPoolExecutor(max_workers=max_workers,
                            thread_name_prefix=log_prefix) as ex:
        future_to_job = {
            ex.submit(_safe_runner, runner, job): job for job in jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            result, error = future.result()
            results.append((job, result, error))
            if on_result:
                try:
                    on_result(job, result, error)
                except Exception:
                    logger.exception(
                        f"[{log_prefix}] on_result callback raised"
                    )

    return results


def _safe_runner(runner, job):
    """Wrap the runner so worker threads never leak exceptions."""
    try:
        return runner(job), None
    except Exception as e:
        logger.exception("llm-pool runner raised")
        return None, str(e)[:300]
