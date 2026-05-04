"""Helpers for the conflict-awareness pipeline:

  - Stable dedup keys + deterministic pupdate IDs
  - Severity constants
  - AST + heuristic method extraction
  - Workspace snapshot from a git worktree (committed + staged diffs)
  - Maiko-owned file filter (so per-task scaffolding doesn't trip
    every-agent-vs-every-agent overlaps)
  - UnionFind helper
  - DB-side dedup check (`_already_escalated`)

Imported by both `detect.py` and `act.py` — kept in its own file so
those two stay short and focused on their respective concerns.
"""

import hashlib
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta

from planet_maiko.models.pupdate import Pupdate

logger = logging.getLogger(__name__)


def _conflict_key(agents, file_name):
    """Stable, order-independent dedup key for a (agent_set, file) conflict."""
    return f"{':'.join(sorted(str(a) for a in agents))}|{file_name}"


def _pupdate_id(kind, conflict_key):
    """Deterministic Pupdate.id derived from the conflict key.

    `kind` distinguishes escalation vs duplicate vs warning so they
    don't collide on the same PK. The hash keeps the id short enough
    to fit Pupdate.id's 64-char column.
    """
    h = hashlib.sha256(conflict_key.encode()).hexdigest()[:16]
    return f"conflict-{kind}-{h}"


def _source_id(conflict_key):
    return f"conflict/{conflict_key}"


# Grace period before a dismissed conflict escalation can re-fire.
# Gives the user room to actually coordinate the agents without the
# pupdate nagging them back into existence every 5-minute brain cycle.
_RE_OPEN_AFTER = timedelta(hours=6)


# Severity levels for file overlaps
SEVERITY_SAME_FILE = "soft"       # Different methods in same file
SEVERITY_SAME_METHOD = "hard"     # Same method in same file
SEVERITY_SAME_LINES = "stop"      # Overlapping line ranges


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


# Files Maiko itself writes into every prepared worktree. They show up
# in every agent's git diff and would cause a "conflict" between every
# pair of agents on TASK.md / CLAUDE.md / .claude/settings.json / etc.
# None of them are real conflicts — they're per-task scaffolding.
_MAIKO_OWNED_FILES = frozenset({
    "TASK.md",
    "CLAUDE.md",
    ".mcp.json",
    ".maiko-env.json",
    "agent.log",
    "REVIEW.md",          # written by the review agent into its own worktree
    "INVESTIGATION.md",   # ditto for investigation agents
})

_MAIKO_OWNED_PREFIXES = (
    ".claude/",   # session settings + hook config Maiko writes per-worktree
    ".maiko-",    # any future .maiko-* scaffolding files
)


def _is_maiko_managed(filepath):
    """True for files Maiko writes into every worktree.

    These are per-task scaffolding (TASK.md describing this task,
    CLAUDE.md with the protocol, .claude/settings.json with hooks,
    etc.) — not user code. They show up in every agent's diff and
    cause false-positive conflict warnings.
    """
    if not filepath:
        return False
    # Normalize Windows-style separators so the prefix check works
    # regardless of how git emits the path.
    norm = filepath.replace("\\", "/")
    if os.path.basename(norm) in _MAIKO_OWNED_FILES:
        return True
    return any(norm.startswith(p) for p in _MAIKO_OWNED_PREFIXES)


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

        # Merge and deduplicate (no unstaged layer), then strip out
        # the per-task scaffolding Maiko writes into every worktree.
        # Without this filter, every agent's diff includes TASK.md,
        # CLAUDE.md, .claude/settings.json etc., and awareness flags
        # every pair of agents as conflicting on those files.
        merged = list(dict.fromkeys(committed_files + staged_files))
        all_files = [f for f in merged if not _is_maiko_managed(f)]

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


def _already_escalated(conflict_key):
    """True if an escalation pupdate exists for this conflict and
    either hasn't been dismissed OR was dismissed within the grace
    window (so we respect the user's "I'm handling it" signal).
    """
    pup = Pupdate.query.filter_by(source_id=_source_id(conflict_key)).first()
    if pup is None:
        return False
    if not pup.dismissed:
        return True
    dismissed_at = pup.dismissed_at
    if dismissed_at is None:
        return False
    if dismissed_at.tzinfo is None:
        dismissed_at = dismissed_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dismissed_at) < _RE_OPEN_AFTER
