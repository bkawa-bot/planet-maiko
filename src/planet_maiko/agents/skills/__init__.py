"""Skill prompt registry.

Each skill is a structured prompt that the brain session executes.
Skills can reference context data (pupdates, tasks, calendar, etc.)
that gets injected into the prompt at runtime.
"""

from planet_maiko.agents.skills.prompts import SKILL_PROMPTS


def get_skill_prompt(skill_name, context):
    """Build a full prompt for a skill with injected context.

    Args:
        skill_name: the skill to run
        context: dict of data to inject into the prompt

    Returns:
        str prompt, or None if skill not found
    """
    template = SKILL_PROMPTS.get(skill_name)
    if template is None:
        return None

    # Inject context into the prompt template
    try:
        return template.format(**context)
    except KeyError:
        # If context is missing keys, just pass the template as-is
        return template


def list_skills():
    """List all available skills with descriptions."""
    return [
        {"name": name, "description": prompt.split("\n")[0].strip("# ").strip()}
        for name, prompt in SKILL_PROMPTS.items()
    ]
