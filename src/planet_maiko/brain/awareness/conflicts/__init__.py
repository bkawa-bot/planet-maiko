"""Agent awareness — detects conflicts between agents working on overlapping code.

Computes workspace snapshots from git diffs, then checks pairwise
for file overlaps and shared methods. Sends A2A (agent-to-agent)
warnings through the agent inbox system or, optionally, kicks off
a full LLM-driven resolution pass.

Edge types:
    file_overlap:    Two agents editing the same files
    api_dependency:  One agent modifying an API that another consumes

Dedup posture:
    Every detected conflict has a stable key — `{sorted_agents}:{file}`
    — and a deterministic pupdate ID derived from it. The cycle runs
    every 5 minutes and will keep re-detecting the same overlap until
    code actually changes; we rely on:

    1. DB lookup by source_id before creating the escalation pupdate.
       If one exists and isn't dismissed, skip — agents + user have
       already been warned for this exact conflict.
    2. If the user dismissed the escalation, leave it dismissed. The
       auto-resolution path below still re-opens a new one if the
       underlying overlap changes (new file, new agent pair, etc.).

    No more in-memory `_resolved_conflicts` set — that was resetting
    on every server restart and re-spawning every previous conflict
    as a fresh pupdate.

Split into:
  - detect.py   pure detection: snapshot worktrees, return pairwise edges
  - act.py      side-effecting actors: send_conflict_warnings,
                resolve_conflicts (LLM-driven), _act_on_resolution
  - _helpers.py git/AST extraction, dedup keys, severity, UnionFind
"""

from .detect import detect_conflicts  # noqa: F401
from .act import send_conflict_warnings, resolve_conflicts  # noqa: F401
