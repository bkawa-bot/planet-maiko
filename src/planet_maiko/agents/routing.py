"""Model + effort + runtime routing — picks the LLM tier, reasoning
depth, and which AgentRuntime to use for a given task.

Three parallel dicts in config (all under ``routing``):

  * ``routing.rules``         — model (haiku/sonnet/opus or
                                runtime-specific like "llama3.1:8b").
  * ``routing.effort_rules``  — reasoning budget (low/medium/high/max).
                                Claude maps this to ``--effort``;
                                Ollama maps it to temperature +
                                max_tokens.
  * ``routing.runtime_rules`` — which runtime serves this task
                                ("claude-code" / "claude-code-tmux" /
                                "ollama"). When unset, falls back to
                                ``brain.runtime`` (the default
                                runtime for spawn/resume).

All three are tunable from Settings → Model Routing.

Effort is *cost-conscious tuning*. Pure classifications and creative
riffs (triage, scene, agent bio gen, theme generation) get "low"
effort because deep reasoning doesn't help them. Real work (coding
agents, training, synthesizing rules from real PRs) gets "high" or
"max" because reasoning depth visibly improves output.

Runtime routing is *cost-pool tuning*. Maiko's internal LLM calls
(overview, scene note, agent bios) default to a local Ollama-served
model so they don't burn Anthropic credit. Coding/review/etc.
agents default to whichever Claude-based runtime the user picked.
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


# Per-task runtime preference. None / not listed means "use the
# default runtime configured in brain.runtime" — which is where the
# Claude-based agent spawn/resume work goes. Listing a task here
# routes its synchronous LLM calls (send / send_json) to a different
# runtime. Use this for cheap internal work where Anthropic-tier
# reasoning is overkill.
DEFAULT_RUNTIME = {
    # Internal Maiko work — local-model defaults so these don't burn
    # Anthropic credit. The user has to actually have Ollama running
    # for these to fire; if it's offline, _get_runtime falls back to
    # the default runtime automatically.
    "scene":                "ollama",
    "agent_bio":            "ollama",
    "skill:theme":          "ollama",

    # skill:home-overview stays on the default (claude) runtime —
    # multi-source synthesis (calendar + pupdates + tasks + signals
    # into a narrative paragraph) is exactly the kind of work Opus
    # is meaningfully better at than llama 3.x. Reverted from
    # Ollama default after real usage showed the output quality
    # wasn't acceptable. User can still route it to Ollama
    # explicitly via Settings → Model Routing if they want.

    # Everything else falls through to brain.runtime, which keeps
    # coding / review / investigation / cartograph on Claude.
}


def resolve_model(task_type, runtime_name=None):
    """Resolve which model to use for a given task type.

    Precedence:
      1. ``routing.runtime_models[runtime_name][task_type]``  — per-runtime
         override; lets a user say "use llama3.1:70b for the overview
         when it's routed to Ollama, but opus when it's routed to
         Claude."
      2. ``routing.runtime_models[runtime_name][prefix]`` — prefix match
         inside the per-runtime override (e.g. ``skill`` covers
         ``skill:home-overview``).
      3. ``routing.rules[task_type]`` — global model rules.
      4. ``routing.rules[prefix]``.
      5. ``DEFAULT_ROUTING[task_type]`` / prefix.
      6. ``routing.default_model``.
      7. None — caller (or runtime) picks its own default.

    The ``runtime_name`` arg is optional. Callers that don't pass it
    get the old behavior (global rules only). Callers that pass it
    benefit from per-runtime model namespaces — necessary because
    Claude tier names (haiku / sonnet / opus) don't translate to
    Ollama model names (llama3.1:8b / qwen2.5:32b / etc.).
    """
    if runtime_name:
        config = load_config()
        routing = config.get("routing", {})
        if not routing.get("enabled", True):
            return None
        per_runtime = (routing.get("runtime_models") or {}).get(runtime_name) or {}
        if task_type in per_runtime:
            return per_runtime[task_type]
        if ":" in task_type:
            prefix = task_type.split(":")[0]
            if prefix in per_runtime:
                return per_runtime[prefix]

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


def resolve_runtime(task_type):
    """Pick which runtime should handle a synchronous task.

    Returns a runtime name ("claude-code" / "claude-code-tmux" /
    "ollama"), or None when the task isn't explicitly routed — the
    caller (``_get_runtime``) interprets None as "use the default
    runtime from ``brain.runtime``."

    Precedence:
      1. ``routing.runtime_rules[task_type]`` (user config)
      2. ``routing.runtime_rules[prefix]`` (prefix match in user config)
      3. ``DEFAULT_RUNTIME[task_type]``
      4. ``DEFAULT_RUNTIME[prefix]``
      5. None (= use default runtime)

    Unlike resolve_model / resolve_effort, the built-in defaults
    here are sparse — they only cover Maiko's internal calls.
    Everything else returns None so the default runtime applies.
    """
    config = load_config()
    routing = config.get("routing", {})
    if not routing.get("enabled", True):
        return None

    user_rules = routing.get("runtime_rules") or {}

    # User-config rules win over built-in defaults.
    if task_type in user_rules:
        return user_rules[task_type]
    if ":" in task_type:
        prefix = task_type.split(":")[0]
        if prefix in user_rules:
            return user_rules[prefix]

    if task_type in DEFAULT_RUNTIME:
        return DEFAULT_RUNTIME[task_type]
    if ":" in task_type:
        prefix = task_type.split(":")[0]
        if prefix in DEFAULT_RUNTIME:
            return DEFAULT_RUNTIME[prefix]

    return None


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
