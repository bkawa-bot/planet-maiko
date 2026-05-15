"""Persistent session store: task_id -> {session_id, working_path}.

Backed by a JSON file in the Maiko data dir so mappings survive
server restarts. Lazy-loaded on first read, flushed on every write.
"""

import json
import os

from planet_maiko.paths import data_dir

_SESSIONS_FILENAME = "agent-sessions.json"
_agent_sessions = None  # loaded lazily by _get_sessions()


def _sessions_path():
    return os.path.join(data_dir(), _SESSIONS_FILENAME)


def _get_sessions():
    """Return the sessions dict, loading from disk on first access."""
    global _agent_sessions
    if _agent_sessions is None:
        path = _sessions_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _agent_sessions = json.load(f)
            except Exception:
                _agent_sessions = {}
        else:
            _agent_sessions = {}
    return _agent_sessions


def _save_sessions():
    """Flush the sessions dict to disk."""
    path = _sessions_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_agent_sessions, f, indent=2)


def _set_session(task_id, session_id, working_path=""):
    """Store a session mapping and persist to disk."""
    sessions = _get_sessions()
    sessions[task_id] = {"session_id": session_id, "working_path": working_path}
    _save_sessions()
