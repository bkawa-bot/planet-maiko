"""Side-effecting actors for detected conflicts.

`send_conflict_warnings` does the cheap thing — drops a one-shot A2A
warning into each agent's inbox + a deterministic-id pupdate so future
cycles dedupe. `resolve_conflicts` does the expensive thing — ask each
agent (via the LLM runtime) what they're doing, classify the overlap,
escalate to the user only if it's genuinely incompatible.
"""

import logging

from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.models.pupdate import Pupdate

from ._helpers import (
    _conflict_key, _pupdate_id, _source_id, _already_escalated,
)

logger = logging.getLogger(__name__)


def send_conflict_warnings(conflicts):
    """Send A2A warnings for detected conflicts.

    Sends one AgentMessage per agent per conflict, but only the first
    time this exact conflict is seen — checked against an existing
    escalation pupdate with a deterministic source_id. Without this
    guard, every 5-minute brain cycle re-sent the same warning into
    each agent's inbox for as long as the files stayed in both diffs.

    Returns:
        int: number of warnings actually sent (not counting dedupes)
    """
    warnings_sent = 0

    for conflict in conflicts:
        agents = conflict["agents"]
        severity = conflict.get("severity", "soft")
        file_name = conflict.get("file", "unknown")
        overlapping = conflict.get("overlapping_methods", [])

        conflict_key = _conflict_key(agents, file_name)
        if _already_escalated(conflict_key):
            continue

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

        # Record a light-touch escalation pupdate so the next cycle's
        # `_already_escalated` check sees it. Uses the same source_id
        # the full `_act_on_resolution` escalation uses — either path
        # going first will dedupe the other.
        db.session.add(Pupdate(
            id=_pupdate_id("warning", conflict_key),
            source="maiko",
            source_id=_source_id(conflict_key),
            type="conflict_warning",
            priority={"stop": "urgent", "hard": "high", "soft": "normal"}.get(severity, "normal"),
            title=f"Conflict warning: agents editing {file_name}",
            body=f"{len(agents)} agents share {file_name}. Warnings sent to each agent's inbox.",
            actionable=False,
            tags=list(agents) + [file_name, "conflict"],
        ))

    if warnings_sent:
        db.session.commit()
        logger.info(f"[awareness] Sent {warnings_sent} conflict warning(s)")

    return warnings_sent


def resolve_conflicts(conflicts):
    """Attempt A2A resolution for detected conflicts.

    For each conflict, asks both agents what they're doing,
    then has them classify the overlap. Only escalates to the
    user if it's a genuine conflict.

    Returns:
        dict with counts: {resolved, escalated, failed}
    """
    try:
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime("conflict_resolution")
        if not runtime.is_available():
            return {"resolved": 0, "escalated": 0, "failed": 0, "skipped": "runtime unavailable"}
    except Exception:
        return {"resolved": 0, "escalated": 0, "failed": 0, "skipped": "runtime error"}

    stats = {"resolved": 0, "escalated": 0, "failed": 0, "skipped": 0}

    for conflict in conflicts:
        agents = conflict["agents"]
        file_name = conflict.get("file", "unknown")

        # For multi-agent clusters, resolve pairwise between first two
        if len(agents) < 2:
            continue
        agent_a = agents[0]
        agent_b = agents[1]

        # DB-backed dedup: if we've already escalated this conflict
        # (either via the LLM resolution below or via the simpler
        # warning path), don't re-run the expensive LLM calls. The
        # pupdate will auto-close when the overlap disappears (no
        # conflict → no pupdate → source_id query misses → re-eligible).
        conflict_key = _conflict_key(agents, file_name)
        if _already_escalated(conflict_key):
            stats["skipped"] += 1
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
        # At least one says conflict -- escalate to user. Deterministic
        # id means a second detection of the same conflict upserts
        # instead of creating a duplicate pupdate.
        conflict_key = _conflict_key([agent_a, agent_b], files_str)
        pup_id = _pupdate_id("escalation", conflict_key)
        src_id = _source_id(conflict_key)

        existing = db.session.get(Pupdate, pup_id)
        if existing is None:
            db.session.add(Pupdate(
                id=pup_id,
                source="maiko",
                source_id=src_id,
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
            ))
            stats["escalated"] += 1
            logger.info("[awareness] Conflict escalated to user")

    elif "duplicate" in (result_a, result_b):
        # One or both say duplicate -- notify user, suggest one stops
        who_stops = agent_b if result_a == "duplicate" else agent_a
        who_continues = agent_a if who_stops == agent_b else agent_b

        conflict_key = _conflict_key([agent_a, agent_b], files_str)
        pup_id = _pupdate_id("duplicate", conflict_key)
        src_id = _source_id(conflict_key)

        # Skip the whole branch — messages + pupdate — if we've
        # already flagged this as duplicate work. One reminder per
        # conflict, not one per cycle.
        if db.session.get(Pupdate, pup_id) is not None:
            return

        # Tell the one who should stop
        db.session.add(AgentMessage(
            task_id=who_stops, direction="to_agent", sender="maiko",
            message_type="conflict_directive",
            content=f"Heads up -- another agent ({who_continues}) is doing similar work on {files_str}. "
                    f"Consider pausing to avoid duplicate effort. Check with your human!",
        ))

        # Tell the one who continues
        db.session.add(AgentMessage(
            task_id=who_continues, direction="to_agent", sender="maiko",
            message_type="conflict_resolved",
            content=f"The other agent ({who_stops}) has been notified about duplicate work on {files_str}. "
                    f"You can keep going -- they'll coordinate with you.",
        ))

        db.session.add(Pupdate(
            id=pup_id,
            source="maiko",
            source_id=src_id,
            type="conflict_duplicate",
            priority="normal",
            title=f"Duplicate work detected: {agent_a} & {agent_b}",
            body=f"Both agents are working on similar changes to {files_str}. Suggested {who_stops} pause.",
            tags=[agent_a, agent_b, "duplicate"],
        ))
        stats["escalated"] += 1
        logger.info(f"[awareness] Duplicate -- {who_stops} told to pause")
