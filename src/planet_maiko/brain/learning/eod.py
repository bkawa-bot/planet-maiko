"""EOD gathering - end-of-day ritual that collects and synthesizes learnings.

State machine:
    idle → gathering (signal agents)
    → reviewing (user reviews collected learnings)
    → synthesized (dedupe, conflict detection, proposed rules)
    → finalized (approved learnings → global pool)
    → idle

Agent learnings come in via pupdates (type=agent_learnings) or
agent messages. The EOD system collects them, deduplicates, detects
conflicts, and presents them for review before merging into the
global learning pool.
"""

import logging
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.signal import Signal
from planet_maiko.models.learning import Learning

logger = logging.getLogger(__name__)

# In-memory EOD state (persisted to DB via a dedicated pupdate)
_eod_state = {
    "status": "idle",  # idle, gathering, reviewing, synthesized, finalized
    "date": None,
    "triggered_at": None,
    "collected": [],  # raw learnings from agents
    "synthesis": None,
    "agents_reported": [],
}


def get_state():
    """Get current EOD state."""
    return dict(_eod_state)


def start_gathering():
    """Begin the EOD gathering process.

    Sends a signal to all active agents asking them to report learnings.
    """
    global _eod_state
    now = datetime.now(timezone.utc)

    _eod_state = {
        "status": "gathering",
        "date": now.strftime("%Y-%m-%d"),
        "triggered_at": now.isoformat(),
        "collected": [],
        "synthesis": None,
        "agents_reported": [],
    }

    # Create an EOD signal pupdate (agents watch for this)
    signal_pupdate = Pupdate(
        id=f"eod-signal-{now.strftime('%Y%m%d')}",
        source="maiko",
        source_id=f"eod/{now.strftime('%Y-%m-%d')}",
        type="eod_signal",
        priority="normal",
        title="End of day: share your learnings",
        body="Report any learnings, patterns, or gotchas you discovered today.",
        actionable=True,
        action_hint="Report learnings",
        tags=["eod"],
    )
    db.session.add(signal_pupdate)
    db.session.commit()

    logger.info("[eod] Started gathering")
    return _eod_state


def collect_from_agents():
    """Collect learnings from agent pupdates.

    Looks for pupdates with type=agent_learnings that arrived
    after the gathering was triggered.
    """
    if _eod_state["status"] != "gathering":
        return {"error": "Not in gathering state"}

    triggered = datetime.fromisoformat(_eod_state["triggered_at"])

    learning_pupdates = (
        Pupdate.query
        .filter(
            Pupdate.source == "agent",
            Pupdate.type.in_(["agent_learnings", "agent_review_feedback"]),
            Pupdate.timestamp >= triggered,
        )
        .all()
    )

    for p in learning_pupdates:
        agent_id = None
        for tag in (p.tags or []):
            if tag.startswith("agent-") or tag.startswith("task-"):
                agent_id = tag
                break

        if agent_id and agent_id not in _eod_state["agents_reported"]:
            _eod_state["agents_reported"].append(agent_id)

        # Parse learnings from the pupdate body
        if p.body:
            for line in p.body.strip().split("\n"):
                line = line.strip().lstrip("- ").strip()
                if line and len(line) > 10:
                    _eod_state["collected"].append({
                        "text": line,
                        "source_agent": agent_id,
                        "source_type": "agent_discovery",
                        "category": _guess_category(line),
                    })

    _eod_state["status"] = "reviewing"
    logger.info(f"[eod] Collected {len(_eod_state['collected'])} learnings from {len(_eod_state['agents_reported'])} agent(s)")
    return _eod_state


def add_manual_learning(text, category="domain_knowledge"):
    """Add a manual learning during the review phase."""
    if _eod_state["status"] not in ("reviewing", "gathering"):
        return {"error": "Not in reviewing state"}

    _eod_state["collected"].append({
        "text": text,
        "source_agent": None,
        "source_type": "manual_input",
        "category": category,
    })
    return {"added": text}


def synthesize():
    """Deduplicate, detect conflicts, and propose rules.

    Returns synthesis result with duplicates, conflicts, and proposed rules.
    """
    if _eod_state["status"] != "reviewing":
        return {"error": "Not in reviewing state"}

    collected = _eod_state["collected"]
    synthesis = {
        "duplicates_merged": 0,
        "conflicts": [],
        "already_known": [],
        "proposed_rules": [],
        "unique_learnings": [],
    }

    # Deduplicate by first 80 chars
    seen = {}
    unique = []
    for item in collected:
        key = item["text"][:80].lower()
        if key in seen:
            synthesis["duplicates_merged"] += 1
        else:
            seen[key] = item
            unique.append(item)

    # Cross-reference with existing learnings
    for item in unique:
        existing = Learning.query.filter(
            Learning.rule.ilike(f"%{item['text'][:40]}%"),
            Learning.status != "dismissed",
        ).first()

        if existing:
            synthesis["already_known"].append({
                "text": item["text"],
                "existing_rule": existing.rule,
                "existing_id": existing.id,
            })
        else:
            synthesis["unique_learnings"].append(item)

    # Detect action-oriented learnings as proposed rules
    action_words = {"always", "never", "prefer", "use", "avoid", "don't", "should", "must"}
    for item in synthesis["unique_learnings"]:
        first_word = item["text"].split()[0].lower() if item["text"] else ""
        if first_word in action_words:
            synthesis["proposed_rules"].append({
                "text": item["text"],
                "category": item["category"],
                "source_agent": item.get("source_agent"),
            })

    _eod_state["synthesis"] = synthesis
    _eod_state["status"] = "synthesized"

    logger.info(
        f"[eod] Synthesis: {len(synthesis['unique_learnings'])} unique, "
        f"{synthesis['duplicates_merged']} dupes, "
        f"{len(synthesis['proposed_rules'])} proposed rules"
    )
    return synthesis


def finalize(decisions=None):
    """Merge approved learnings into the global pool.

    Args:
        decisions: dict mapping learning text → "keep" | "drop"
                   (if None, all unique learnings are kept)
    """
    if _eod_state["status"] != "synthesized":
        return {"error": "Not in synthesized state"}

    decisions = decisions or {}
    synthesis = _eod_state["synthesis"]
    stats = {"kept": 0, "dropped": 0, "rules_created": 0}

    for item in synthesis["unique_learnings"]:
        decision = decisions.get(item["text"], "keep")

        if decision == "drop":
            stats["dropped"] += 1
            continue

        # Create signal
        signal = Signal(
            category=item["category"],
            text=item["text"],
            source_type=item.get("source_type", "agent_discovery"),
            repo=item.get("repo"),
        )
        db.session.add(signal)

        # Check if this should be a direct learning (proposed rule)
        is_rule = any(r["text"] == item["text"] for r in synthesis.get("proposed_rules", []))
        if is_rule:
            learning = Learning(
                rule=item["text"],
                category=item["category"],
                confidence=0.5,
                signal_count=1,
                source="eod",
                status="pending",
                last_signal_at=datetime.now(timezone.utc),
            )
            db.session.add(learning)
            stats["rules_created"] += 1

        stats["kept"] += 1

    db.session.commit()

    _eod_state["status"] = "finalized"
    logger.info(f"[eod] Finalized: {stats}")
    return stats


def reset():
    """Reset EOD state back to idle."""
    global _eod_state
    _eod_state = {
        "status": "idle",
        "date": None,
        "triggered_at": None,
        "collected": [],
        "synthesis": None,
        "agents_reported": [],
    }


def _guess_category(text):
    """Simple heuristic to guess a learning's category from its text."""
    lower = text.lower()
    if any(w in lower for w in ("null", "undefined", "optional", "nullable")):
        return "null_safety"
    if any(w in lower for w in ("error", "exception", "catch", "try")):
        return "error_handling"
    if any(w in lower for w in ("test", "assert", "mock", "fixture")):
        return "testing"
    if any(w in lower for w in ("perf", "slow", "cache", "optimize", "latency")):
        return "performance"
    if any(w in lower for w in ("api", "endpoint", "route", "request", "response")):
        return "api_design"
    if any(w in lower for w in ("security", "auth", "token", "credential", "secret")):
        return "security"
    if any(w in lower for w in ("name", "rename", "variable", "convention")):
        return "naming"
    return "domain_knowledge"
