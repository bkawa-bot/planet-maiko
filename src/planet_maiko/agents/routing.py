"""Model routing — picks the right model tier for each task type."""

from planet_maiko.config import load_config

DEFAULT_ROUTING = {
    # Haiku tier
    "triage": "haiku",
    "classify": "haiku",
    "scene": "haiku",
    "conflict_query": "haiku",

    # Sonnet tier
    "chat": "sonnet",
    "skill": "sonnet",
    "skill:morning-brief": "sonnet",
    "skill:brainstorm": "sonnet",
    "skill:investigate": "sonnet",
    "skill:pr-review": "sonnet",
    "skill:plan": "sonnet",
    "project_plan": "sonnet",
    "project_tasks": "sonnet",
    "conflict_classify": "sonnet",
    "profile_judge": "sonnet",

    # Opus tier
    "tournament:entry": "opus",
    "tournament:judge": "opus",
    "training:entry": "opus",
    "training:judge": "opus",
    "coding_agent": "opus",
}


def resolve_model(task_type):
    """Resolve which model to use for a given task type.

    Checks config routing.rules first, falls back to DEFAULT_ROUTING,
    then tries prefix match (e.g. "skill:morning-brief" -> "skill"),
    then falls back to config default_model or None.
    """
    config = load_config()
    routing = config.get("routing", {})

    if not routing.get("enabled", True):
        return None

    rules = routing.get("rules", DEFAULT_ROUTING)

    # Exact match
    if task_type in rules:
        return rules[task_type]

    # Prefix match
    if ":" in task_type:
        prefix = task_type.split(":")[0]
        if prefix in rules:
            return rules[prefix]

    # Default
    return routing.get("default_model", None)
