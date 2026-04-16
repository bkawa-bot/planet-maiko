"""Format the sign-off agents append to external-facing messages
(PR descriptions, PR comments, etc.) so reviewers know the post is
from an agent rather than the human owner.

Internal Maiko messages (pupdates, in-app chat, agent outbox) are
never signed — only text the agent is about to post to an external
system like GitHub.
"""

from planet_maiko.database import db
from planet_maiko.models.agent_profile import AgentProfile


# Mirrors frontend/src/components/AssignAgentModal.jsx AVATAR_EMOJI.
# Keep the two in sync whenever a new avatar lands.
AVATAR_EMOJI = {
    "shiba": "🐕", "corgi": "🐶", "husky": "🐺", "poodle": "🐩", "golden": "🦮",
    "beagle": "🐕‍🦺", "dalmatian": "🐾", "samoyed": "☁️", "akita": "🐕",
    "pomeranian": "🧸",
    "calico_cat": "🐱", "tabby_cat": "🐈", "black_cat": "🐈‍⬛",
    "bunny": "🐰", "hamster": "🐹", "fox": "🦊",
}
DEFAULT_EMOJI = "🐾"
PLANET_EMOJI = "🪐"


def emoji_for_avatar(avatar):
    return AVATAR_EMOJI.get((avatar or "").lower(), DEFAULT_EMOJI)


def _resolve_profile(profile_or_id):
    if profile_or_id is None:
        return None
    if isinstance(profile_or_id, AgentProfile):
        return profile_or_id
    if isinstance(profile_or_id, str):
        return db.session.get(AgentProfile, profile_or_id)
    return None


def format_agent_signature(profile_or_id):
    """Return the signature line, or None if we can't resolve the agent.

    We intentionally don't fall back to a generic "an agent" line —
    if we don't know who it is, we'd rather not sign than lie.
    """
    profile = _resolve_profile(profile_or_id)
    if not profile:
        return None
    name = (profile.display_name or "").strip()
    if not name:
        return None
    return (
        f"— from agent {name} {emoji_for_avatar(profile.avatar)}, "
        f"resident of Planet Maiko {PLANET_EMOJI}"
    )


def sign_external_message(body, profile_or_id):
    """Append the signature to body. Idempotent: re-signing the same
    body doesn't stack signatures. If the profile can't be resolved,
    returns body unchanged.
    """
    sig = format_agent_signature(profile_or_id)
    if not sig:
        return body
    body = body or ""
    if sig in body:
        return body
    return f"{body.rstrip()}\n\n{sig}\n"


def signature_instruction_for_agent(profile_or_id):
    """Prompt snippet we hand to the coding agent so it signs any
    external post it writes itself (PR body, PR comments). Returns
    empty string when we can't resolve the profile — better to stay
    silent than prompt for a generic sign-off.
    """
    sig = format_agent_signature(profile_or_id)
    if not sig:
        return ""
    return (
        "\n\nSign-off: when you write a PR description or any comment "
        "on GitHub/Linear that you author, end the body with this "
        "line on its own line so reviewers know it's from you rather "
        f"than the human owner:\n\n    {sig}\n"
    )
