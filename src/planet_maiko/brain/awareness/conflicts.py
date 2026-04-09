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
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.models.pupdate import Pupdate

logger = logging.getLogger(__name__)

# Severity levels for file overlaps
SEVERITY_SAME_FILE = "soft"       # Different methods in same file
SEVERITY_SAME_METHOD = "hard"     # Same method in same file
SEVERITY_SAME_LINES = "stop"      # Overlapping line ranges


# ---------------------------------------------------------------------------
# 4B: AST-based method extraction
# ---------------------------------------------------------------------------

def _extract_methods_ast(file_path, language=None):
    """Extract method/function names with line ranges using tree-sitter AST parsing.

    Returns: dict of {method_name: (start_line, end_line)}
    Falls back to empty dict if parsing fails.
    """
    if language is None:
        ext = os.path.splitext(file_path)[1].lower()
        lang_map = {".java": "java", ".py": "python", ".js": "javascript", ".ts": "typescript"}
        language = lang_map.get(ext)

    if language is None:
        return {}

    try:
        if language == "java":
            import tree_sitter_java as tsjava
            from tree_sitter import Language, Parser
            lang = Language(tsjava.language())
            parser = Parser(lang)

            with open(file_path, "rb") as f:
                tree = parser.parse(f.read())

            methods = {}

            # Walk tree to find method_declaration nodes
            def walk(node):
                if node.type == "method_declaration":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        methods[name_node.text.decode()] = (node.start_point[0], node.end_point[0])
                elif node.type == "constructor_declaration":
                    name_node = node.child_by_field_name("name")
                    if name_node:
                        methods[name_node.text.decode()] = (node.start_point[0], node.end_point[0])
                for child in node.children:
                    walk(child)
            walk(tree.root_node)
            return methods

        # Python fallback using built-in ast
        elif language == "python":
            import ast
            with open(file_path, "r") as f:
                tree = ast.parse(f.read())
            methods = {}
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[node.name] = (node.lineno, node.end_lineno or node.lineno)
            return methods
    except Exception:
        return {}

    return {}


# ---------------------------------------------------------------------------
# 4A: Workspace snapshot with committed + staged + unstaged diffs
# ---------------------------------------------------------------------------

def _run_git(args, cwd, timeout=10):
    """Run a git command and return stdout lines, or [] on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return []
        return [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
    except Exception:
        return []


def _extract_methods_heuristic(diff_output):
    """Extract method names from diff @@ context markers (original heuristic).

    Returns a set of method name strings.
    """
    methods = set()
    for line in diff_output.split("\n"):
        if line.startswith("@@") and "@@" in line[2:]:
            context = line.split("@@")[-1].strip()
            if context:
                methods.add(context.split("(")[0].strip())
    return methods


def _is_config_file(filepath):
    """Check whether a file path looks like a shared config file worth flagging.

    Excludes generated/lock files that commonly show up in diffs but
    aren't real conflict sources.
    """
    name = os.path.basename(filepath).lower()

    # Skip generated/lock files
    skip = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
            "composer.lock", "gemfile.lock", ".gitignore", "tsconfig.json"}
    if name in skip:
        return False

    return any(
        filepath.endswith(ext)
        for ext in (".yaml", ".yml", ".toml", ".env", ".config")
    ) or name in ("pom.xml", "build.gradle", "settings.gradle")


def _get_workspace_snapshot(worktree_path):
    """Get the files an agent is working on from git diff.

    Captures three layers of changes:
        1. Committed diff (HEAD~1..HEAD or origin/main...HEAD)
        2. Staged but uncommitted (git diff --cached)
        3. Unstaged working-tree changes (git diff)

    Returns:
        dict with files, methods  (or None on failure)
    """
    if not os.path.isdir(worktree_path):
        return None

    try:
        # Use committed + staged diffs only. Unstaged changes are noise
        # (editor temp files, uncommitted experiments, etc.) and cause
        # false positive conflict warnings.

        # --- Layer 1: committed diff vs main branch ---
        committed_files = _run_git(
            ["diff", "--name-only", "origin/main...HEAD"], cwd=worktree_path,
        )
        if not committed_files:
            committed_files = _run_git(
                ["diff", "--name-only", "HEAD~1..HEAD"], cwd=worktree_path,
            )

        # --- Layer 2: staged but uncommitted ---
        staged_files = _run_git(
            ["diff", "--cached", "--name-only"], cwd=worktree_path,
        )

        # Merge and deduplicate (no unstaged layer)
        all_files = list(dict.fromkeys(committed_files + staged_files))

        # --- Method extraction per file ---
        methods = {}
        for f in all_files:
            abs_path = os.path.join(worktree_path, f)

            # Try AST-based extraction first (4B) for files on disk
            if os.path.isfile(abs_path):
                ast_methods = _extract_methods_ast(abs_path)
                if ast_methods:
                    methods[f] = list(ast_methods.keys())
                    continue

            # Fall back to @@ heuristic from committed diff
            file_methods = set()
            for diff_args in (
                ["diff", "-U0", "HEAD~1..HEAD", "--", f],
                ["diff", "-U0", "origin/main...HEAD", "--", f],
                ["diff", "-U0", "--cached", "--", f],
                ["diff", "-U0", "--", f],
            ):
                try:
                    result = subprocess.run(
                        ["git"] + diff_args,
                        cwd=worktree_path, capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode == 0 and result.stdout:
                        file_methods |= _extract_methods_heuristic(result.stdout)
                except Exception:
                    pass

            if file_methods:
                methods[f] = list(file_methods)

        return {
            "files": all_files,
            "methods": methods,
        }
    except Exception as e:
        logger.warning(f"[awareness] Failed to snapshot {worktree_path}: {e}")
        return None


# ---------------------------------------------------------------------------
# 4C: Union-Find for conflict clustering
# ---------------------------------------------------------------------------

class UnionFind:
    """Simple Union-Find for conflict clustering."""

    def __init__(self):
        self.parent = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[rx] = ry


def detect_conflicts(agent_worktrees):
    """Detect file/method conflicts between active agents using clustering.

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


# ---------------------------------------------------------------------------
# Warning / resolution system (updated for multi-agent conflict format)
# ---------------------------------------------------------------------------

def send_conflict_warnings(conflicts):
    """Send A2A warnings for detected conflicts.

    Sends messages to all agents involved in each conflict,
    through the agent inbox system.

    Returns:
        int: number of warnings sent
    """
    warnings_sent = 0

    for conflict in conflicts:
        agents = conflict["agents"]
        severity = conflict.get("severity", "soft")
        file_name = conflict.get("file", "unknown")
        overlapping = conflict.get("overlapping_methods", [])

        priority_map = {"stop": "urgent", "hard": "high", "soft": "normal"}
        priority = priority_map.get(severity, "normal")

        for agent_id in agents:
            other_agents = [a for a in agents if a != agent_id]
            others_str = ", ".join(str(a) for a in other_agents)

            if severity == "stop":
                detail = "STOP - overlapping line changes detected!"
            elif severity == "hard":
                if overlapping:
                    detail = (
                        f"Same methods detected ({', '.join(overlapping)}) - "
                        "coordinate before pushing!"
                    )
                else:
                    detail = "Config/shared file overlap - coordinate before pushing!"
            else:
                detail = "Different areas of the same file."

            msg = AgentMessage(
                task_id=agent_id,
                direction="to_agent",
                sender="maiko",
                message_type="conflict_warning",
                content=(
                    f"[{severity.upper()}] Agent(s) {others_str} also editing: "
                    f"{file_name}. {detail}"
                ),
            )
            db.session.add(msg)
            warnings_sent += 1

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
        agents = conflict["agents"]
        file_name = conflict.get("file", "unknown")

        # For multi-agent clusters, resolve pairwise between first two
        if len(agents) < 2:
            continue
        agent_a = agents[0]
        agent_b = agents[1]

        # Skip if already resolved
        conflict_key = f"{agent_a}:{agent_b}:{file_name}"
        if conflict_key in _resolved_conflicts:
            continue

        logger.info(f"[awareness] Resolving conflict: {agent_a} <-> {agent_b} on {file_name}")

        # Step 1: Ask each agent what they're doing
        query_prompt = (
            f"Two agents are editing the same file: {file_name}\n\n"
            f"Briefly describe what you are changing in this file.\n\n"
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
            f"You are editing: {file_name}\n"
            f"Your work: {desc_a}\n\n"
            f"Another agent is also editing the same file.\n"
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
            file_name, conflict, stats,
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
        # Both agree it's fine -- tell them to keep going, don't bother user
        for task_id in [agent_a, agent_b]:
            msg = AgentMessage(
                task_id=task_id, direction="to_agent", sender="maiko",
                message_type="conflict_resolved",
                content=f"Checked with the other agent -- your changes to {files_str} are compatible. Keep going!",
            )
            db.session.add(msg)
        stats["resolved"] += 1
        logger.info("[awareness] Compatible -- no user notification needed")

    elif "conflict" in (result_a, result_b):
        # At least one says conflict -- escalate to user
        pupdate = Pupdate(
            id=f"conflict-escalation-{agent_a}-{agent_b}-{uuid.uuid4().hex[:8]}",
            source="maiko",
            source_id=f"conflict/{agent_a}/{agent_b}",
            type="conflict_escalation",
            priority="high",
            title=f"Conflict: {agent_a} vs {agent_b} on {files_str}",
            body=(
                f"**Agent A** ({agent_a}): {desc_a}\n"
                f"Classification: {result_a} -- {reason_a}\n\n"
                f"**Agent B** ({agent_b}): {desc_b}\n"
                f"Classification: {result_b} -- {reason_b}\n\n"
                f"These agents need your help resolving this conflict."
            ),
            actionable=True,
            action_hint="Resolve conflict",
            tags=[agent_a, agent_b, "conflict"],
        )
        db.session.add(pupdate)
        stats["escalated"] += 1
        logger.info("[awareness] Conflict escalated to user")

    elif "duplicate" in (result_a, result_b):
        # One or both say duplicate -- notify user, suggest one stops
        who_stops = agent_b if result_a == "duplicate" else agent_a
        who_continues = agent_a if who_stops == agent_b else agent_b

        # Tell the one who should stop
        msg_stop = AgentMessage(
            task_id=who_stops, direction="to_agent", sender="maiko",
            message_type="conflict_directive",
            content=f"Heads up -- another agent ({who_continues}) is doing similar work on {files_str}. "
                    f"Consider pausing to avoid duplicate effort. Check with your human!",
        )
        db.session.add(msg_stop)

        # Tell the one who continues
        msg_continue = AgentMessage(
            task_id=who_continues, direction="to_agent", sender="maiko",
            message_type="conflict_resolved",
            content=f"The other agent ({who_stops}) has been notified about duplicate work on {files_str}. "
                    f"You can keep going -- they'll coordinate with you.",
        )
        db.session.add(msg_continue)

        # Notify user
        pupdate = Pupdate(
            id=f"conflict-dup-{agent_a}-{agent_b}-{uuid.uuid4().hex[:8]}",
            source="maiko",
            type="conflict_duplicate",
            priority="normal",
            title=f"Duplicate work detected: {agent_a} & {agent_b}",
            body=f"Both agents are working on similar changes to {files_str}. Suggested {who_stops} pause.",
            tags=[agent_a, agent_b, "duplicate"],
        )
        db.session.add(pupdate)
        stats["escalated"] += 1
        logger.info(f"[awareness] Duplicate -- {who_stops} told to pause")
