"""Skill prompt registry.

Skills are stored in the database (custom_skills table) so users
can view, edit, and create their own. Default skills are seeded
on first run.
"""

import logging

logger = logging.getLogger(__name__)


def seed_defaults():
    """Seed default skills into the database if they don't exist."""
    from planet_maiko.database import db
    from planet_maiko.models.custom_skill import CustomSkill
    from planet_maiko.agents.skills.defaults import DEFAULT_SKILLS

    for s in DEFAULT_SKILLS:
        existing = db.session.get(CustomSkill, s["id"])
        if not existing:
            skill = CustomSkill(
                id=s["id"],
                name=s["name"],
                description=s["description"],
                prompt=s["prompt"],
                mcps=s.get("mcps", []),
                icon=s.get("icon", "wand"),
                is_default=True,
            )
            db.session.add(skill)
            logger.info(f"[skills] Seeded default skill: {s['name']}")

    db.session.commit()


def get_skill_prompt(skill_name, context):
    """Build a full prompt for a skill with injected context.

    Tries database first, falls back to hardcoded prompts.
    """
    try:
        from planet_maiko.models.custom_skill import CustomSkill
        skill = CustomSkill.query.get(skill_name)
        if skill:
            template = skill.prompt
        else:
            from planet_maiko.agents.skills.prompts import SKILL_PROMPTS
            template = SKILL_PROMPTS.get(skill_name)
    except Exception:
        from planet_maiko.agents.skills.prompts import SKILL_PROMPTS
        template = SKILL_PROMPTS.get(skill_name)

    if template is None:
        return None

    result = template
    for key, value in context.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def list_skills():
    """List all available skills."""
    try:
        from planet_maiko.models.custom_skill import CustomSkill
        skills = CustomSkill.query.order_by(CustomSkill.is_default.desc(), CustomSkill.name).all()
        if skills:
            return [s.to_dict() for s in skills]
    except Exception:
        pass

    from planet_maiko.agents.skills.prompts import SKILL_PROMPTS
    return [
        {"id": name, "name": name, "description": prompt.split("\n")[0].strip("# ").strip()}
        for name, prompt in SKILL_PROMPTS.items()
    ]
