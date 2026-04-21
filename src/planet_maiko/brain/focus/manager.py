"""Focus mode manager - gates what notifications surface based on focus state.

Focus states:
    available   - everything surfaces
    soft_focus  - only critical + high priority
    deep_focus  - only critical
    away        - only critical

Pupdates that don't pass the gate are "held" and delivered as a
digest when the user exits focus mode.

Focus can be set explicitly or auto-triggered by calendar events.
"""

import logging
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate

logger = logging.getLogger(__name__)

# What priority levels surface in each state
GATE_MATRIX = {
    "available": {"critical", "urgent", "high", "normal", "low"},
    "soft_focus": {"critical", "urgent", "high"},
    "deep_focus": {"critical", "urgent"},
    "away": {"critical", "urgent"},
}

# Pupdate types that are always critical regardless of priority field
CRITICAL_TYPES = {
    "deploy_rollback", "error_spike", "incident",
    "agent_stuck", "agent_need_input",
}

# Time escalation: held items escalate priority after N minutes
ESCALATION_MINUTES = {
    "low": 120,
    "normal": 45,
    "high": 90,
}

# Focus state
_state = {
    "current_state": "available",
    "entered_at": None,
    "trigger": None,  # "explicit" or "calendar"
    "duration_minutes": None,
    "expires_at": None,
    "held_count": 0,
}


def get_state():
    """Get current focus state."""
    _check_expiry()
    return dict(_state)


def set_state(new_state, duration_minutes=None, trigger="explicit"):
    """Set focus state.

    Args:
        new_state: available, soft_focus, deep_focus, away
        duration_minutes: auto-expire after this many minutes (optional)
        trigger: "explicit" (user set it) or "calendar" (auto from meeting)
    """
    if new_state not in GATE_MATRIX:
        raise ValueError(f"Invalid state: {new_state}. Must be one of: {list(GATE_MATRIX.keys())}")

    now = datetime.now(timezone.utc)

    # If leaving focus, deliver held pupdates
    if _state["current_state"] != "available" and new_state == "available":
        _release_held()

    _state["current_state"] = new_state
    _state["entered_at"] = now.isoformat()
    _state["trigger"] = trigger
    _state["duration_minutes"] = duration_minutes
    _state["expires_at"] = (now + timedelta(minutes=duration_minutes)).isoformat() if duration_minutes else None

    logger.info(f"[focus] State changed to {new_state} (trigger={trigger}, duration={duration_minutes}min)")
    return get_state()


def should_surface(pupdate):
    """Check if a pupdate should surface given the current focus state.

    Returns:
        True if the pupdate passes the gate, False if it should be held.
    """
    _check_expiry()

    state = _state["current_state"]
    allowed = GATE_MATRIX[state]

    # Critical types always surface
    if pupdate.type in CRITICAL_TYPES:
        return True

    # Check escalated priority
    effective_priority = _get_effective_priority(pupdate)

    return effective_priority in allowed


def hold_pupdate(pupdate):
    """Mark a pupdate as held (not surfaced due to focus mode)."""
    pupdate.extra = {**(pupdate.extra or {}), "held": True, "held_at": datetime.now(timezone.utc).isoformat()}
    _state["held_count"] += 1


def get_held():
    """Get all held pupdates."""
    held = Pupdate.query.filter(
        Pupdate.dismissed == False,
    ).all()
    return [p for p in held if (p.extra or {}).get("held")]


def get_digest():
    """Compile a digest of held pupdates, grouped by urgency.

    Returns:
        dict with needs_attention (critical/high) and can_wait (normal/low)
    """
    held = get_held()

    needs_attention = []
    can_wait = []

    for p in held:
        effective = _get_effective_priority(p)
        item = {
            "id": p.id,
            "title": p.title,
            "source": p.source,
            "priority": p.priority,
            "effective_priority": effective,
            "type": p.type,
            "timestamp": p.timestamp.isoformat() if p.timestamp else None,
        }
        if effective in ("critical", "urgent", "high"):
            needs_attention.append(item)
        else:
            can_wait.append(item)

    return {
        "needs_attention": needs_attention,
        "can_wait": can_wait,
        "total_held": len(held),
        "focus_duration_minutes": _get_focus_duration(),
    }


def check_calendar_focus(pupdates):
    """Auto-set focus based on upcoming meetings."""
    now = datetime.now(timezone.utc)

    for p in pupdates:
        if p.source != "calendar":
            continue
        start_str = (p.extra or {}).get("start")
        end_str = (p.extra or {}).get("end")
        if not start_str:
            continue

        try:
            start = datetime.fromisoformat(start_str)
            # Meeting starting within 5 minutes
            if timedelta(0) <= (start - now) <= timedelta(minutes=5):
                current = get_state()
                if current.get("current_state") == "available":
                    set_state("soft_focus", trigger="calendar")
                    return True

            # Meeting ended (if end time exists)
            if end_str:
                end = datetime.fromisoformat(end_str)
                if timedelta(0) <= (now - end) <= timedelta(minutes=2):
                    current = get_state()
                    if current.get("current_state") == "soft_focus":
                        set_state("available", trigger="calendar")
                        return True
        except (ValueError, TypeError):
            continue
    return False


def _check_expiry():
    """Auto-expire focus mode if duration has passed."""
    if _state["expires_at"]:
        expires = datetime.fromisoformat(_state["expires_at"])
        if datetime.now(timezone.utc) >= expires:
            logger.info("[focus] Focus mode expired, returning to available")
            set_state("available", trigger="expiry")


def _get_effective_priority(pupdate):
    """Get effective priority, accounting for time escalation."""
    if pupdate.type in CRITICAL_TYPES:
        return "critical"

    priority = pupdate.priority
    held_at = (pupdate.extra or {}).get("held_at")

    if held_at:
        held_time = datetime.fromisoformat(held_at)
        minutes_held = (datetime.now(timezone.utc) - held_time).total_seconds() / 60
        escalation = ESCALATION_MINUTES.get(priority, 999)

        if minutes_held > escalation:
            # Escalate: low → normal → high → critical
            escalation_order = ["low", "normal", "high", "critical"]
            idx = escalation_order.index(priority) if priority in escalation_order else 0
            if idx < len(escalation_order) - 1:
                return escalation_order[idx + 1]

    return priority


def _release_held():
    """Release held pupdates when exiting focus mode."""
    held = get_held()
    for p in held:
        extra = p.extra or {}
        extra.pop("held", None)
        extra.pop("held_at", None)
        p.extra = extra

    if held:
        db.session.commit()
        logger.info(f"[focus] Released {len(held)} held pupdate(s)")

    _state["held_count"] = 0


def _get_focus_duration():
    """Get how long the user has been in focus mode."""
    if _state["entered_at"] and _state["current_state"] != "available":
        entered = datetime.fromisoformat(_state["entered_at"])
        return round((datetime.now(timezone.utc) - entered).total_seconds() / 60)
    return 0
