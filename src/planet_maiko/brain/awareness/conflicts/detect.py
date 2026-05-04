"""Pure detection: snapshot every active worktree, cross-index by file,
return pairwise overlap edges. NO side effects (no pupdates, no agent
messages, no DB writes). Callers that need warnings call into act.py
explicitly.
"""

from datetime import datetime, timezone

from ._helpers import _get_workspace_snapshot, _is_config_file


def detect_conflicts(agent_worktrees, focus_worktree_path=None):
    """Detect file/method conflicts between active agents using clustering.

    Pure function — snapshots each worktree via git diff, cross-indexes
    the file sets, and returns the pairwise overlap edges. NO side
    effects (no pupdates, no messages, no DB writes). Callers that need
    warnings must call send_conflict_warnings() explicitly; the brain
    cycle does this in phase 2, the /sessions/<id>/conflicts endpoint
    deliberately does not.

    Args:
        agent_worktrees: list of dicts with {task_id, worktree_path}
        focus_worktree_path: optional absolute path. When provided, the
            returned conflicts are filtered to only those that involve
            the agent whose worktree matches this path — every other
            worktree in the list is still snapshotted (otherwise there
            would be nothing to conflict *with*), but pairs that don't
            touch the focus worktree are dropped from the result. Used
            by the external-session on-demand query endpoint so a tool
            asking "am I about to collide with anyone?" gets only its
            own edges, not a global conflict dump.

    Returns:
        list of conflict edges
    """
    # Get snapshots
    snapshots = {}
    # Map task_id -> worktree_path so we can figure out which agent
    # owns the focus path after snapshotting.
    task_paths = {}
    for agent in agent_worktrees:
        task_id = agent["task_id"]
        task_paths[task_id] = agent.get("worktree_path")
        snapshot = _get_workspace_snapshot(agent["worktree_path"])
        if snapshot and snapshot["files"]:
            snapshots[task_id] = snapshot

    if len(snapshots) < 2:
        return []

    focus_task_ids = None
    if focus_worktree_path:
        # Multiple sessions could technically register the same path
        # (weird, but nothing stops it) — match all of them.
        focus_task_ids = {
            tid for tid, path in task_paths.items()
            if path == focus_worktree_path
        }
        if not focus_task_ids:
            # Focus worktree isn't in the scan population — no possible
            # conflicts to return. Avoid producing a confusing global
            # list just because the focus filter missed.
            return []

    # Build file-to-agents inverted index
    file_to_agents = {}
    for agent_id, snap in snapshots.items():
        for f in snap.get("files", []):
            file_to_agents.setdefault(f, []).append(agent_id)

    # Direct pairwise conflicts only — no transitive clustering.
    # If A and B share file X, and B and C share file Y, that does NOT
    # mean A and C are in conflict.
    conflicts = []

    for f, involved in file_to_agents.items():
        if len(involved) < 2:
            continue

        if focus_task_ids is not None and not (focus_task_ids & set(involved)):
            # Focused query: skip edges the focus agent isn't part of.
            continue

        # Check method overlap (AST-based or heuristic)
        methods_by_agent = {}
        for a in involved:
            methods_by_agent[a] = set(snapshots[a].get("methods", {}).get(f, []))

        all_methods = set()
        overlapping_methods = set()
        for a, methods in methods_by_agent.items():
            overlapping_methods |= (all_methods & methods)
            all_methods |= methods

        if _is_config_file(f):
            severity = "hard"
        elif overlapping_methods:
            severity = "hard"
        else:
            severity = "soft"

        conflicts.append({
            "agents": involved,
            "file": f,
            "severity": severity,
            "overlapping_methods": list(overlapping_methods),
            "detected_at": datetime.now(timezone.utc).isoformat(),
        })

    return conflicts
