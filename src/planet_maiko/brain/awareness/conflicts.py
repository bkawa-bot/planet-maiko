"""Agent awareness - detects conflicts between agents working on overlapping code.

Computes workspace snapshots from git diffs, then checks pairwise
for file overlaps and API dependencies. Sends A2A (agent-to-agent)
warnings through the agent inbox system.

Edge types:
    file_overlap:    Two agents editing the same files
    api_dependency:  One agent modifying an API that another consumes
"""

import logging
import os
import subprocess
from itertools import combinations
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.models.pupdate import Pupdate

logger = logging.getLogger(__name__)

# Severity levels for file overlaps
SEVERITY_SAME_FILE = "soft"       # Different methods in same file
SEVERITY_SAME_METHOD = "hard"     # Same method in same file
SEVERITY_SAME_LINES = "stop"      # Overlapping line ranges


def _get_workspace_snapshot(worktree_path):
    """Get the files an agent is working on from git diff.

    Returns:
        dict with files_changed, methods_changed
    """
    if not os.path.isdir(worktree_path):
        return None

    try:
        # Get changed files vs main branch
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1..HEAD"],
            cwd=worktree_path, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            # Try against origin/main
            result = subprocess.run(
                ["git", "diff", "--name-only", "origin/main...HEAD"],
                cwd=worktree_path, capture_output=True, text=True, timeout=10,
            )

        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]

        # Get changed methods/functions (rough heuristic from diff)
        methods = {}
        for f in files:
            try:
                diff = subprocess.run(
                    ["git", "diff", "-U0", "origin/main...HEAD", "--", f],
                    cwd=worktree_path, capture_output=True, text=True, timeout=10,
                )
                file_methods = set()
                for line in diff.stdout.split("\n"):
                    # Heuristic: lines starting with @@ often contain function context
                    if line.startswith("@@") and "@@" in line[2:]:
                        context = line.split("@@")[-1].strip()
                        if context:
                            file_methods.add(context.split("(")[0].strip())
                if file_methods:
                    methods[f] = list(file_methods)
            except Exception:
                pass

        return {
            "files": files,
            "methods": methods,
        }
    except Exception as e:
        logger.warning(f"[awareness] Failed to snapshot {worktree_path}: {e}")
        return None


def detect_conflicts(agent_worktrees):
    """Detect conflicts between active agents.

    Args:
        agent_worktrees: list of dicts with {task_id, worktree_path}

    Returns:
        list of conflict edges
    """
    # Get snapshots
    snapshots = {}
    for agent in agent_worktrees:
        task_id = agent["task_id"]
        snapshot = _get_workspace_snapshot(agent["worktree_path"])
        if snapshot and snapshot["files"]:
            snapshots[task_id] = snapshot

    if len(snapshots) < 2:
        return []

    conflicts = []

    # Pairwise comparison
    for (id_a, snap_a), (id_b, snap_b) in combinations(snapshots.items(), 2):
        files_a = set(snap_a["files"])
        files_b = set(snap_b["files"])

        shared_files = files_a & files_b
        if not shared_files:
            continue

        # Determine severity
        severity = SEVERITY_SAME_FILE
        shared_methods = {}

        for f in shared_files:
            methods_a = set(snap_a.get("methods", {}).get(f, []))
            methods_b = set(snap_b.get("methods", {}).get(f, []))
            overlap = methods_a & methods_b
            if overlap:
                severity = SEVERITY_SAME_METHOD
                shared_methods[f] = list(overlap)

        # Config file overlap is especially risky
        config_files = [f for f in shared_files if any(
            f.endswith(ext) for ext in (".yaml", ".yml", ".json", ".toml", ".env", ".config")
        )]

        conflicts.append({
            "agents": [id_a, id_b],
            "type": "file_overlap",
            "severity": severity,
            "shared_files": list(shared_files),
            "shared_methods": shared_methods,
            "config_overlap": config_files,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        })

    return conflicts


def send_conflict_warnings(conflicts):
    """Send A2A warnings for detected conflicts.

    Sends messages to both agents involved in each conflict,
    through the agent inbox system.

    Returns:
        int: number of warnings sent
    """
    warnings_sent = 0

    for conflict in conflicts:
        agent_a, agent_b = conflict["agents"]
        severity = conflict["severity"]
        shared = conflict["shared_files"][:5]  # Limit for readability
        files_str = ", ".join(shared)

        priority_map = {"stop": "urgent", "hard": "high", "soft": "normal"}
        priority = priority_map.get(severity, "normal")

        # Message for agent A about agent B
        msg_to_a = AgentMessage(
            task_id=agent_a,
            direction="to_agent",
            sender="maiko",
            message_type="conflict_warning",
            content=f"[{severity.upper()}] Agent working on {agent_b} is also editing: {files_str}. "
                    + ("Same methods detected - coordinate before pushing!" if severity == "hard"
                       else "STOP - overlapping line changes detected!" if severity == "stop"
                       else "Different areas of the same files."),
        )
        db.session.add(msg_to_a)

        # Message for agent B about agent A
        msg_to_b = AgentMessage(
            task_id=agent_b,
            direction="to_agent",
            sender="maiko",
            message_type="conflict_warning",
            content=f"[{severity.upper()}] Agent working on {agent_a} is also editing: {files_str}. "
                    + ("Same methods detected - coordinate before pushing!" if severity == "hard"
                       else "STOP - overlapping line changes detected!" if severity == "stop"
                       else "Different areas of the same files."),
        )
        db.session.add(msg_to_b)
        warnings_sent += 2

    if warnings_sent:
        db.session.commit()
        logger.info(f"[awareness] Sent {warnings_sent} conflict warning(s)")

    return warnings_sent
