"""Parse structured blocks out of one-shot agent output.

Review / investigation agents have no CLI; their "communication" is a
set of PATTERN:, PROPOSAL:, and CONFIDENCE: blocks embedded in their
response text. This module finds those blocks, emits the matching
DB rows (Signal, Pupdate), and returns the cleaned text (blocks
stripped) so the output stored for the user reads naturally.

Shared by _phase_execute_agent_tasks in brain/cycle.py.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


_VALID_CATEGORIES = {
    "security", "error_handling", "testing", "performance",
    "api_design", "architecture", "null_safety", "style", "naming",
    "docs", "pattern", "domain_knowledge", "gotcha", "team",
}
_VALID_PRIORITIES = {"urgent", "high", "normal", "low"}
_VALID_CONFIDENCE = {"high", "medium", "low"}


# Grab blocks that start at the beginning of a line with the keyword
# and continue until the next blank line-terminated section. The
# non-greedy body grabs everything until a double-newline followed by
# (another keyword, a heading, or end-of-string) — close-enough for
# free-form model output.
_BLOCK_TERMINATORS = (
    r"(?=\nPATTERN:|\nPROPOSAL:|\nTASK:|\nCONFIDENCE:|\n#|\Z)"
)

_PATTERN_RE = re.compile(
    r"^PATTERN:\s*(?P<header>.+?)\n(?P<body>[\s\S]*?)" + _BLOCK_TERMINATORS,
    re.MULTILINE,
)
# TASK: is a semantic alias for PROPOSAL: — agents who intuit "propose
# a follow-up task" reach for the TASK keyword more naturally than
# PROPOSAL, and both land in the same approve/edit/dismiss queue. The
# parser runs the union regex below and treats them identically.
_PROPOSAL_RE = re.compile(
    r"^(?:PROPOSAL|TASK):\s*(?P<title>.+?)\n(?P<body>[\s\S]*?)" + _BLOCK_TERMINATORS,
    re.MULTILINE,
)
_CONFIDENCE_RE = re.compile(
    r"^CONFIDENCE:\s*(?P<level>\w+)\s*\n(?P<body>[\s\S]*?)" + _BLOCK_TERMINATORS,
    re.MULTILINE,
)


def _parse_kv_body(body):
    """Parse `key: value` lines + `key:` followed by a `---` fenced
    multi-line value. Returns a dict.
    """
    out = {}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line:
            i += 1
            continue
        m = re.match(r"^\s*([a-zA-Z_]+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key = m.group(1).strip().lower()
        value = m.group(2).strip()
        # Fenced multi-line value: `key:` alone on a line, then lines
        # bounded by `---` delimiters OR indented.
        if not value and i + 1 < len(lines) and lines[i + 1].strip() == "---":
            i += 2
            buf = []
            while i < len(lines) and lines[i].strip() != "---":
                buf.append(lines[i])
                i += 1
            value = "\n".join(buf).strip()
            i += 1  # skip closing ---
        elif not value:
            # Indented continuation — grab subsequent indented lines
            buf = []
            i += 1
            while i < len(lines) and (lines[i].startswith("  ") or lines[i].startswith("\t")):
                buf.append(lines[i].strip())
                i += 1
            value = " ".join(buf).strip()
        else:
            i += 1
        out[key] = value
    return out


def _extract_category_from_header(header):
    """Pull `[category]` out of 'PATTERN: [null_safety] rule text'."""
    m = re.match(r"^\s*\[([a-z_]+)\]\s*(.+)$", header)
    if m:
        cat = m.group(1)
        rule = m.group(2).strip()
        if cat in _VALID_CATEGORIES:
            return cat, rule
    return "pattern", header.strip()


def parse_and_apply_blocks(output, *, agent, task, repo=None):
    """Scan the agent's output for structured blocks, create Signals /
    Proposal pupdates for each, and return the cleaned output with
    those blocks stripped.

    Args:
        output: the raw text the agent returned.
        agent: AgentProfile instance (for attribution).
        task: Task instance (for linking).
        repo: optional repo string to attach to emitted signals.

    Returns:
        dict with keys:
            cleaned_output: str — output with blocks removed
            patterns_emitted: int
            proposals_emitted: int
            confidence: "high"|"medium"|"low"|None
    """
    from planet_maiko.database import db
    from planet_maiko.models.signal import Signal
    from planet_maiko.models.pupdate import Pupdate

    if not output:
        return {"cleaned_output": "", "patterns_emitted": 0, "proposals_emitted": 0, "confidence": None}

    patterns_emitted = 0
    proposals_emitted = 0
    confidence = None

    # PATTERN: blocks → Signal rows (will flow through next cycle's
    # cluster_signals_into_learnings pass like any other signal).
    for m in _PATTERN_RE.finditer(output):
        header = m.group("header")
        body = m.group("body")
        category, rule = _extract_category_from_header(header)
        fields = _parse_kv_body(body)
        file_path = fields.get("file") or None
        code = fields.get("code") or None
        try:
            sig = Signal(
                category=category,
                text=rule[:500],
                source_type="pr_comment",  # same path as PR-scraped signals
                reviewer=agent.display_name,
                severity="suggestion",
                repo=repo or fields.get("repo") or agent.scope_repo,
                file_path=file_path,
                code_context=code,
                examples=[{
                    "path": file_path,
                    "diff_hunk": code,
                    "author": agent.display_name,
                    "line": None,
                }] if code else [],
                # Review / investigation agents already wrote the rule
                # text themselves; no re-synthesis needed.
                synthesized=True,
            )
            db.session.add(sig)
            patterns_emitted += 1
        except Exception as e:
            logger.warning(f"[agent-output] Failed to persist PATTERN: {e}")

    # PROPOSAL: blocks → agent_proposal pupdates (land in From Maiko
    # for approval; the user can turn them into tasks with one click).
    for m in _PROPOSAL_RE.finditer(output):
        title = m.group("title").strip()
        fields = _parse_kv_body(m.group("body"))
        priority = fields.get("priority", "normal").lower()
        if priority not in _VALID_PRIORITIES:
            priority = "normal"
        try:
            draft = {
                "title": title[:200],
                "type": "todo",
                "priority": priority,
                "repo": fields.get("repo") or repo or agent.scope_repo or "",
                "category": fields.get("category") or "",
                "description": fields.get("description") or "",
            }
            proposal = Pupdate(
                id=f"proposal-{uuid.uuid4().hex[:10]}",
                source="maiko",
                type="agent_proposal",
                priority=priority,
                title=title[:200],
                body=draft["description"],
                actionable=True,
                action_hint="Approve / edit / dismiss",
                tags=["proposal", "from_maiko", agent.id],
                extra={
                    "from_agent_id": agent.id,
                    "from_task_id": task.id,
                    "draft": draft,
                },
            )
            db.session.add(proposal)
            proposals_emitted += 1
        except Exception as e:
            logger.warning(f"[agent-output] Failed to persist PROPOSAL: {e}")

    # CONFIDENCE: hedges (investigation agents only usually)
    conf_match = _CONFIDENCE_RE.search(output)
    if conf_match:
        level = conf_match.group("level").strip().lower()
        if level in _VALID_CONFIDENCE:
            confidence = level

    # Strip every block from the output so the user-facing artifact
    # reads as a clean report without the machine-parsed sections.
    cleaned = output
    for regex in (_PATTERN_RE, _PROPOSAL_RE, _CONFIDENCE_RE):
        cleaned = regex.sub("", cleaned)
    # Collapse 3+ consecutive blank lines that the stripping may leave.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if patterns_emitted or proposals_emitted or confidence:
        logger.info(
            f"[agent-output] {agent.display_name} ({task.id}): "
            f"{patterns_emitted} patterns, {proposals_emitted} proposals, "
            f"confidence={confidence}"
        )

    return {
        "cleaned_output": cleaned,
        "patterns_emitted": patterns_emitted,
        "proposals_emitted": proposals_emitted,
        "confidence": confidence,
    }
