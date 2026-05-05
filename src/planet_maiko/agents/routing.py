"""Model + effort routing — picks model tier and reasoning depth per task type.

Two parallel dicts: `routing.rules` chooses the model (haiku/sonnet/opus),
`routing.effort_rules` chooses Claude's `--effort` budget (low/medium/high/max).
Both are tunable from Settings → Model Routing.

Effort is *cost-conscious tuning*. Pure classifications and creative riffs
(triage, scene, agent bio gen, theme generation) get "low" effort because
deep reasoning doesn't help them. Real work (coding agents, training,
synthesizing rules from real PRs) gets "high" or "max" because reasoning
depth visibly improves output. Defaults below; user overrides win.
"""

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
    "skill:home-overview": "opus",
    "skill:brainstorm": "sonnet",
    "skill:investigate": "sonnet",
    "skill:pr-review": "sonnet",
    "skill:plan": "sonnet",
    "project_plan": "sonnet",
    "project_tasks": "sonnet",
    "conflict_classify": "sonnet",
    "profile_judge": "sonnet",

    # Opus tier
    "training:entry": "opus",
    "training:judge": "opus",
    "synthetic_data": "opus",
    "coding_agent": "opus",
}


# Per-task reasoning depth. Three categories:
#
#   - low    Creative or classification work that doesn't benefit from
#            deep reasoning — fast, cheap, "good enough" outputs.
#            Triage, scene generation, conflict matching, agent bios.
#
#   - medium General sonnet work — skills, planning, profile judging.
#            The default for unknown task types.
#
#   - high   Real engineering work that visibly improves with deeper
#            thinking — coding agents, training data extraction,
#            synthetic data generation, the home overview (multi-source
#            synthesis).
#
# Users can flip any of these in Settings, and "max" is available as
# an opt-in tier for anyone who wants premium reasoning at the cost
# of more tokens.
DEFAULT_EFFORT = {
    # Cheap creative / classification — low keeps these snappy and cheap
    "triage": "low",
    "classify": "low",
    "scene": "low",
    "conflict_query": "low",
    "chat": "low",

    # General work — medium is the right middle
    "skill": "medium",
    "skill:brainstorm": "medium",
    "skill:investigate": "medium",
    "skill:pr-review": "medium",
    "skill:plan": "medium",
    "project_plan": "medium",
    "project_tasks": "medium",
    "conflict_classify": "medium",
    "profile_judge": "medium",

    # Heavy lifting — pay for deeper thinking
    "skill:home-overview": "high",
    "training:entry": "high",
    "training:judge": "high",
    "synthetic_data": "high",
    "coding_agent": "high",
}


def resolve_model(task_type):
    """Resolve which model to use for a given task type.

    Checks config routing.rules first, falls back to DEFAULT_ROUTING,
    then tries prefix match (e.g. "skill:morning-brief" -> "skill"),
    then falls back to config default_model or None.
    """
    return _resolve(task_type, "rules", DEFAULT_ROUTING, "default_model")


def resolve_effort(task_type):
    """Resolve `--effort` budget (low|medium|high|max) for a task type.

    Same precedence as resolve_model: config.routing.effort_rules → exact
    match → prefix match → DEFAULT_EFFORT → config.routing.thinking_budget
    (the global default that the existing knob was). Returns None when
    routing is disabled, so the caller falls back to whatever default
    the runtime has baked in.
    """
    return _resolve(task_type, "effort_rules", DEFAULT_EFFORT, "thinking_budget")


def _resolve(task_type, rules_key, defaults, fallback_key):
    """Shared lookup logic for model + effort."""
    config = load_config()
    routing = config.get("routing", {})

    if not routing.get("enabled", True):
        return None

    rules = routing.get(rules_key) or defaults

    # Exact match
    if task_type in rules:
        return rules[task_type]

    # Prefix match
    if ":" in task_type:
        prefix = task_type.split(":")[0]
        if prefix in rules:
            return rules[prefix]

    # Default
    return routing.get(fallback_key, None)
