"""Hardcoded skill prompt fallbacks.

These are the last-resort fallbacks used only when a prompt cannot
be loaded from the database or from the prompts/*.md files.

Prefer editing the .md files in src/planet_maiko/prompts/ instead
of modifying this dict.
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# Skill IDs that have prompt files. home-overview is excluded — it's
# engine plumbing (not a user-facing skill). Its prompt file still
# lives at prompts/home-overview.md and gets loaded via the general
# _load_prompt_file path when brain/overview.py asks for it; it just
# isn't advertised as a runnable skill in the registry or Specialties
# UI. morning-brief + evening-wrap were retired entirely.
_SKILL_IDS = [
    "investigate",
    "repo-analysis",
]


def _build_fallback_prompts():
    """Build prompt dict from .md files (no dependency on __init__)."""
    fallbacks = {}
    for skill_id in _SKILL_IDS:
        prompt_path = _PROMPTS_DIR / f"{skill_id}.md"
        try:
            if prompt_path.is_file():
                fallbacks[skill_id] = prompt_path.read_text(encoding="utf-8")
        except Exception:
            pass
    return fallbacks


SKILL_PROMPTS = _build_fallback_prompts()
