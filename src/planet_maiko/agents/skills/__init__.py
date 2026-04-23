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


# The voice reference is read once and injected into every Maiko-voiced
# prompt as {voice}. Prompts that don't reference {voice} are unchanged
# — the substitution below is a no-op for them. Single source of truth
# for how Maiko speaks; see prompts/voice.md.
_VOICE_CACHE = None


def _voice_reference():
    global _VOICE_CACHE
    if _VOICE_CACHE is None:
        try:
            _VOICE_CACHE = (_PROMPTS_DIR / "voice.md").read_text(encoding="utf-8")
        except Exception:
            _VOICE_CACHE = ""
    return _VOICE_CACHE


def seed_defaults():
    """Seed default skills into the database if they don't exist."""
    from planet_maiko.database import db
    from planet_maiko.models.custom_skill import CustomSkill
    from planet_maiko.agents.skills.defaults import DEFAULT_SKILLS

    # Retirement sweep: morning-brief + evening-wrap are gone
    # (home-overview covers their terrain). pack-insights was
    # renamed to evening-wrap in an earlier migration and is
    # also retired. Drop any existing rows so they stop appearing
    # in the Skills list and no scheduled runs try to fire them.
    for retired_id in ("pack-insights", "morning-brief", "evening-wrap"):
        row = db.session.get(CustomSkill, retired_id)
        if row is not None:
            db.session.delete(row)
            logger.info(f"[skills] Retired skill removed: {retired_id}")
    db.session.commit()

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

    # 1. Try database. Two cases:
    #    - User-created skill (is_default=False): always use the DB prompt,
    #      since there's no file or hardcoded fallback for it.
    #    - User-edited default skill (user_edited=True): prefer their edits
    #      over the bundled file/hardcoded prompt.
    try:
        from planet_maiko.database import db
        from planet_maiko.models.custom_skill import CustomSkill
        skill = db.session.get(CustomSkill, skill_name)
        if skill and (not skill.is_default or skill.user_edited):
            template = skill.prompt
    except Exception as e:
        logger.debug(f"[skills] Could not check DB for prompt of {skill_name}: {e}")

    # 2. Try prompt file (authoritative for default skills unless user-edited)
    if template is None:
        template = _load_prompt_file(skill_name)

    # 3. Fall back to hardcoded prompts
    if template is None:
        from planet_maiko.agents.skills.prompts import SKILL_PROMPTS
        template = SKILL_PROMPTS.get(skill_name)

    if template is None:
        return None

    # Auto-inject the current local date/day so skills always know "when"
    # without relying on the model's guess. Callers can override by passing
    # these keys explicitly in context. Uses the user's configured tz so
    # "current_date" matches their calendar day (the morning brief was
    # dropping to yesterday when the server and user were in different
    # zones).
    from planet_maiko.config import user_now
    now = user_now()
    base_context = {
        "current_date": now.strftime("%Y-%m-%d"),
        "day_of_week": now.strftime("%A"),
        "current_time": now.strftime("%I:%M %p"),
        "voice": _voice_reference(),
    }
    context = {**base_context, **(context or {})}

    # Inject user name from config so Maiko addresses the user correctly.
    # If no name is configured, fall back to "your human" so the LLM never
    # sees a literal {user_name} placeholder and hallucinates a name.
    user_name = ""
    try:
        from planet_maiko.config import load_config
        user_name = load_config().get("user", {}).get("name", "").strip()
    except Exception:
        pass
    context = {**context, "user_name": user_name or "your human"}

    result = template
    for key, value in context.items():
        result = result.replace("{" + key + "}", str(value))

    # If the prompt doesn't reference {user_name} but we have one,
    # prepend a directive so Claude uses the right name.
    if user_name and "{user_name}" not in template and user_name not in result:
        result = f"The user's name is {user_name}. Address them by name when appropriate.\n\n{result}"

    # Safety net: if the template doesn't reference the date anywhere,
    # prepend it so the model never has to guess the current day.
    if "{current_date}" not in template and "{day_of_week}" not in template:
        result = f"Today is {base_context['day_of_week']}, {base_context['current_date']}.\n\n{result}"

    return result


def list_skills():
    """List all available skills."""
    try:
        from planet_maiko.models.custom_skill import CustomSkill
        skills = CustomSkill.query.order_by(CustomSkill.is_default.desc(), CustomSkill.name).all()
        if skills:
            return [s.to_dict() for s in skills]
    except Exception as e:
        logger.warning(f"[skills] DB lookup failed, falling back to hardcoded prompts: {e}")

    from planet_maiko.agents.skills.prompts import SKILL_PROMPTS
    return [
        {"id": name, "name": name, "description": prompt.split("\n")[0].strip("# ").strip()}
        for name, prompt in SKILL_PROMPTS.items()
    ]
