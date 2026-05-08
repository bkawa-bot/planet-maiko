"""Running-subprocess registry for agents.

Lets stop_agent_session() find and kill an in-flight `claude --print`
when the user cancels a job. Keyed by job_id (= AgentJob.id post-
unification).
"""

import logging
import subprocess
import threading

logger = logging.getLogger(__name__)


# the finally block. Not persistent: a server restart drops the map,
# but any still-running agent process would be orphaned by the restart
# anyway (it loses its MCP channel and exits on its own next reply).
_running_processes = {}
_running_processes_lock = threading.Lock()


def register_running_process(job_id, popen):
    with _running_processes_lock:
        _running_processes[job_id] = popen


def unregister_running_process(job_id):
    with _running_processes_lock:
        _running_processes.pop(job_id, None)


def stop_agent_session(job_id, *, grace_seconds=3):
    """Stop the running `claude --print` for this job, if any.

    Returns True if a process was found and terminated, False if no
    process was tracked (already exited, or kickoff never ran).

    Tries a graceful SIGTERM first; falls back to SIGKILL if the
    subprocess doesn't exit within `grace_seconds`. Safe to call even
    when no process exists — cleanup paths can call this unconditionally.
    """
    with _running_processes_lock:
        popen = _running_processes.pop(job_id, None)
    if popen is None:
        return False
    if popen.poll() is not None:
        # Already exited on its own between our check and now.
        return False
    try:
        popen.terminate()
        try:
            popen.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            popen.kill()
            try:
                popen.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning(f"[agent] {job_id}: kill() did not reap, leaving to OS")
    except Exception as e:
        logger.warning(f"[agent] stop_agent_session({job_id}) failed: {e}")
    return True
