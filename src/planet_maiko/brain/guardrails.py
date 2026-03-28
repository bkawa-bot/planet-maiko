"""Guardrails - action permission levels for autonomous behavior.

Three tiers:
    autonomous:         Acts without confirmation
    semi_autonomous:    Acts with logging/notification
    needs_confirmation: Queued for user review in the dashboard
"""

AUTONOMOUS = {
    "mark_read",
    "create_task",
    "dismiss_low_priority",
}

SEMI_AUTONOMOUS = {
    "create_project",
    "advance_project",
    "update_project",
    "prepare_agent",
    "triage_incident",
    "run_skill",
    "send_agent_message",
}

NEEDS_CONFIRMATION = {
    "approve_plan",
    "merge_pr",
    "push_code",
    "dismiss_urgent",
    "delete_task",
    "stop_agent",
    "finalize_eod",
}


def can_act_autonomously(action):
    """Check if an action can be performed without user confirmation."""
    return action in AUTONOMOUS


def is_semi_autonomous(action):
    """Check if an action can be performed with logging."""
    return action in SEMI_AUTONOMOUS


def needs_user_confirmation(action):
    """Check if an action requires explicit user approval."""
    return action in NEEDS_CONFIRMATION


def get_permission_level(action):
    """Get the permission level for an action."""
    if action in AUTONOMOUS:
        return "autonomous"
    if action in SEMI_AUTONOMOUS:
        return "semi_autonomous"
    if action in NEEDS_CONFIRMATION:
        return "needs_confirmation"
    return "unknown"
