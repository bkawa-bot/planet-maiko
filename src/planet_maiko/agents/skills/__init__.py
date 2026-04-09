"""Skill prompt registry.

Skills are stored in the database (custom_skills table) so users
can view, edit, and create their own. Default skills are seeded
on first run.

Prompt resolution order:
  1. Database (user-edited)
  2. Prompt file (src/planet_maiko/prompts/{skill_id}.md)
  3. Hardcoded fallback (prompts.py)
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"  # -> planet_maiko/prompts/


def _load_prompt_file(skill_id):
    """Load a prompt template from the prompts directory.

    Returns the file contents as a string, or None if the file
    doesn't exist or can't be read.
    """
    prompt_path = _PROMPTS_DIR / f"{skill_id}.md"
    try:
        if prompt_path.is_file():
            return prompt_path.read_text(encoding="utf-8")
    except Exception:
        logger.debug("Could not load prompt file %s", prompt_path)
    return None


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

    Resolution order: database -> prompt file -> hardcoded fallback.
    """
    template = None

    # 1. Try database (only if user has edited it)
    try:
        from planet_maiko.models.custom_skill import CustomSkill
        skill = CustomSkill.query.get(skill_name)
        if skill and skill.user_edited:
            template = skill.prompt
    except Exception:
        pass

    # 2. Try prompt file (authoritative for default skills unless user-edited)
    if template is None:
        template = _load_prompt_file(skill_name)

    # 3. Fall back to hardcoded prompts
    if template is None:
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
