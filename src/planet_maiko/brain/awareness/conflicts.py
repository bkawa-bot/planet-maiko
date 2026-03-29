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


# Track resolved conflicts so we don't re-resolve every cycle
_resolved_conflicts = set()


def resolve_conflicts(conflicts):
    """Attempt A2A resolution for detected conflicts.

    For each conflict, asks both agents what they're doing,
    then has them classify the overlap. Only escalates to the
    user if it's a genuine conflict.

    Returns:
        dict with counts: {resolved, escalated, failed}
    """
    try:
        from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
        runtime = ClaudeCodeRuntime()
        if not runtime.is_available():
            return {"resolved": 0, "escalated": 0, "failed": 0, "skipped": "runtime unavailable"}
    except Exception:
        return {"resolved": 0, "escalated": 0, "failed": 0, "skipped": "runtime error"}

    stats = {"resolved": 0, "escalated": 0, "failed": 0}

    for conflict in conflicts:
        agent_a, agent_b = conflict["agents"]
        shared = conflict["shared_files"][:5]
        files_str = ", ".join(shared)

        # Skip if already resolved
        conflict_key = f"{agent_a}:{agent_b}:{files_str}"
        if conflict_key in _resolved_conflicts:
            continue

        logger.info(f"[awareness] Resolving conflict: {agent_a} <-> {agent_b} on {files_str}")

        # Step 1: Ask each agent what they're doing
        query_prompt = (
            f"Two agents are editing the same files: {files_str}\n\n"
            f"Briefly describe what you are changing in these files.\n\n"
            f"Respond with JSON: {{\"summary\": \"brief description\", \"intent\": \"what you're trying to achieve\"}}"
        )

        summary_a = runtime.send_json(query_prompt, timeout=30)
        summary_b = runtime.send_json(query_prompt, timeout=30)

        if not summary_a.get("parsed") or not summary_b.get("parsed"):
            stats["failed"] += 1
            # Fall back to warning
            send_conflict_warnings([conflict])
            continue

        desc_a = summary_a["parsed"].get("summary", "unknown work")
        desc_b = summary_b["parsed"].get("summary", "unknown work")

        # Step 2: Ask each to classify the other's work
        classify_prompt = (
            f"You are editing: {files_str}\n"
            f"Your work: {desc_a}\n\n"
            f"Another agent is also editing the same files.\n"
            f"Their work: {desc_b}\n\n"
            f"Classify this overlap:\n"
            f'- "compatible": changes can coexist, safe to merge later\n'
            f'- "duplicate": you are doing the same work, one should stop\n'
            f'- "conflict": changes are incompatible, need human to decide\n\n'
            f'Respond with JSON: {{"classification": "compatible|duplicate|conflict", "reason": "why"}}'
        )

        class_a = runtime.send_json(classify_prompt, timeout=30)

        # Swap perspectives for agent B
        classify_prompt_b = classify_prompt.replace(
            f"Your work: {desc_a}", f"Your work: {desc_b}"
        ).replace(
            f"Their work: {desc_b}", f"Their work: {desc_a}"
        )
        class_b = runtime.send_json(classify_prompt_b, timeout=30)

        result_a = (class_a.get("parsed") or {}).get("classification", "conflict")
        result_b = (class_b.get("parsed") or {}).get("classification", "conflict")
        reason_a = (class_a.get("parsed") or {}).get("reason", "")
        reason_b = (class_b.get("parsed") or {}).get("reason", "")

        logger.info(f"[awareness] Resolution: A={result_a}, B={result_b}")

        # Step 3: Act on the resolution
        _act_on_resolution(
            agent_a, agent_b, result_a, result_b,
            desc_a, desc_b, reason_a, reason_b,
            files_str, conflict, stats,
        )

        _resolved_conflicts.add(conflict_key)

    if stats["resolved"] or stats["escalated"]:
        db.session.commit()

    return stats


def _act_on_resolution(agent_a, agent_b, result_a, result_b,
                       desc_a, desc_b, reason_a, reason_b,
                       files_str, conflict, stats):
    """Take action based on both agents' classifications."""

    if result_a == "compatible" and result_b == "compatible":
        # Both agree it's fine — tell them to keep going, don't bother user
        for task_id in [agent_a, agent_b]:
            msg = AgentMessage(
                task_id=task_id, direction="to_agent", sender="maiko",
                message_type="conflict_resolved",
                content=f"Checked with the other agent — your changes to {files_str} are compatible. Keep going! 🐾",
            )
            db.session.add(msg)
        stats["resolved"] += 1
        logger.info(f"[awareness] ✅ Compatible — no user notification needed")

    elif "conflict" in (result_a, result_b):
        # At least one says conflict — escalate to user
        pupdate = Pupdate(
            id=f"conflict-escalation-{agent_a}-{agent_b}-{int(datetime.now(timezone.utc).timestamp())}",
            source="maiko",
            source_id=f"conflict/{agent_a}/{agent_b}",
            type="conflict_escalation",
            priority="high",
            title=f"Conflict: {agent_a} vs {agent_b} on {files_str}",
            body=(
                f"**Agent A** ({agent_a}): {desc_a}\n"
                f"Classification: {result_a} — {reason_a}\n\n"
                f"**Agent B** ({agent_b}): {desc_b}\n"
                f"Classification: {result_b} — {reason_b}\n\n"
                f"These agents need your help resolving this conflict."
            ),
            actionable=True,
            action_hint="Resolve conflict",
            tags=[agent_a, agent_b, "conflict"],
        )
        db.session.add(pupdate)
        stats["escalated"] += 1
        logger.info(f"[awareness] 🔴 Conflict escalated to user")

    elif "duplicate" in (result_a, result_b):
        # One or both say duplicate — notify user, suggest one stops
        who_stops = agent_b if result_a == "duplicate" else agent_a
        who_continues = agent_a if who_stops == agent_b else agent_b

        # Tell the one who should stop
        msg_stop = AgentMessage(
            task_id=who_stops, direction="to_agent", sender="maiko",
            message_type="conflict_directive",
            content=f"Heads up — another agent ({who_continues}) is doing similar work on {files_str}. "
                    f"Consider pausing to avoid duplicate effort. Check with your human!",
        )
        db.session.add(msg_stop)

        # Tell the one who continues
        msg_continue = AgentMessage(
            task_id=who_continues, direction="to_agent", sender="maiko",
            message_type="conflict_resolved",
            content=f"The other agent ({who_stops}) has been notified about duplicate work on {files_str}. "
                    f"You can keep going — they'll coordinate with you.",
        )
        db.session.add(msg_continue)

        # Notify user
        pupdate = Pupdate(
            id=f"conflict-dup-{agent_a}-{agent_b}-{int(datetime.now(timezone.utc).timestamp())}",
            source="maiko",
            type="conflict_duplicate",
            priority="normal",
            title=f"Duplicate work detected: {agent_a} & {agent_b}",
            body=f"Both agents are working on similar changes to {files_str}. Suggested {who_stops} pause.",
            tags=[agent_a, agent_b, "duplicate"],
        )
        db.session.add(pupdate)
        stats["escalated"] += 1
        logger.info(f"[awareness] ⚠️ Duplicate — {who_stops} told to pause")
