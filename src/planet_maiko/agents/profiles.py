"""Agent profile management - names, avatars, stats, recommendations.

Agents are characters in your town. They arrive with randomly generated
names, you can pick their avatar, and they grow through experience.
"""

import logging
import random
import threading

from planet_maiko.database import db
from planet_maiko.models.agent_profile import AgentProfile

logger = logging.getLogger(__name__)

# Name pools
NAMES = [
    "Glitch", "Phantom", "Chai", "Nano", "Meow Wow",
    "Angel", "Serow", "Echo", "Flux",
    "Bam", "Blitz", "Aeon", "Vivi", "Void",
    "Xia", "Zero", "Jams", "Mazino",
]

AVATARS = [
    "shiba", "corgi", "husky", "poodle", "golden", "beagle",
    "dalmatian", "samoyed", "akita", "pomeranian",
    "calico_cat", "tabby_cat", "black_cat",
    "bunny", "hamster", "fox",
]

TECH_SUFFIXES = [
    " Bot", ".flow", ".wave", ".exe", "core",
    ".io", " TV", " Drive", " Disk", ".computer",
]

FLAVOR_TEXTS = [
    "Loves debugging. Afraid of CSS.",
    "Is not afraid to test in prod.",
    "Believes every problem is a data structure problem.",
    "Writes tests first, asks questions later.",
    "Has strong opinions about bracket placement.",
    "Thinks documentation is a love language.",
    "Happiest when all tests are green.",
    "Secretly enjoys reading stack traces.",
    "Believes in the power of a good variable name.",
    "Will pair program with anyone who has snacks.",
    "Thinks merge conflicts build character.",
    "Dreams in binary.",
]


def create_profile(agent_id, display_name=None, avatar=None,
                   role="coding", scope_repo=None, instructions=None):
    """Create a new agent profile with a random name and avatar.

    Args:
        agent_id: primary key for the profile.
        display_name: optional; if omitted, a random unused name is picked.
        avatar: optional; random if omitted.
        role: "coding" | "review" | "investigation" (default "coding").
        scope_repo: optional single-repo scope. null = global.
        instructions: optional markdown injected into every session.
    """
    existing = db.session.get(AgentProfile, agent_id)
    if existing:
        return existing

    if not display_name:
        used_names = {p.display_name for p in AgentProfile.query.all()}
        available = [n for n in NAMES if n not in used_names]
        display_name = random.choice(available) if available else f"Agent-{random.randint(100, 999)}"

    display_name += random.choice(TECH_SUFFIXES)

    profile = AgentProfile(
        id=agent_id,
        display_name=display_name,
        avatar=avatar or random.choice(AVATARS),
        flavor_text=random.choice(FLAVOR_TEXTS),
        role=role,
        scope_repo=scope_repo,
        instructions=instructions,
    )
    db.session.add(profile)
    db.session.commit()

    logger.info(f"[profiles] New agent arrived: {display_name} ({agent_id}) role={role} scope={scope_repo}")

    # If the user didn't hand-author instructions, kick off a short
    # first-person arrival bio generation in the background. The
    # profile lands in the UI immediately with flavor_text only; the
    # richer bio fills in ~2-5s later when Haiku returns. Lets each
    # agent feel like its own character instead of a slot skin.
    if not instructions:
        _schedule_bio_generation(agent_id)

    return profile


# ---------------------------------------------------------------------------
# Arrival bios — LLM-written "who I am" paragraph per new agent
# ---------------------------------------------------------------------------

_BIO_PROMPT = """You are a new AI engineering agent joining someone's "pack" of specialists in a tool called Planet Maiko. Introduce yourself.

Write a 3–4 sentence first-person bio that establishes your voice, temperament, and one or two specific preferences or opinions about how you work. Do not describe your role in generic terms. Do not list tools. Do not promise excellence. Read like a real person telling a colleague what they're like to work with.

Examples of the tone to hit (but find your own voice — do not copy):

- "I'm Glitch Drive. I read the whole file before I touch any of it, and I get a little cranky about bare except clauses. If something surprises me in a diff I'll leave a comment instead of guessing."
- "I'm Echo.core. I work on the auth-service repo mostly, and I prefer small commits over one big one. I'd rather ship something boring that works than something clever that scares the next person."
- "I'm Nano.io. I'm fast, sometimes too fast — so I try to re-read my own diff before I claim it's done. I care a lot about error handling; I'd rather pick the wrong exception type than swallow one."

Your details:
- Your name: {name}
- Your role: {role} ({role_description})
- Scope: {scope}

Return ONLY the bio text. No preamble, no markdown fences, no quoted name."""


_ROLE_DESCRIPTIONS = {
    "coding": "you write code changes on a branch; the user reviews them",
    "review": "you review PRs that other people opened, leaving inline comments and a verdict",
    "investigation": "you trace through incidents, error spikes, or repo questions and produce a written report",
    "cartographer": "you map an unfamiliar repo into a navigable overview so future agents know where things live",
}


def _schedule_bio_generation(agent_id):
    """Kick off arrival-bio generation on a daemon thread.

    Never blocks the user-facing create flow. Silently no-ops when
    the LLM runtime isn't available (e.g. during tests or a broken
    install). Writes the result back to profile.instructions.
    """
    try:
        from flask import current_app
        app = current_app._get_current_object()
    except Exception:
        return  # no app context → skip (e.g. during seeding)

    def _run():
        with app.app_context():
            try:
                from planet_maiko.agents.brain_session import _get_runtime
                from planet_maiko.agents.routing import resolve_model

                runtime = _get_runtime()
                if not runtime or not runtime.is_available():
                    return

                profile = db.session.get(AgentProfile, agent_id)
                if not profile or profile.instructions:
                    return  # user edited it in the meantime — don't overwrite

                scope = profile.scope_repo or "whatever repo you drop them into"
                role = profile.role or "coding"
                prompt = _BIO_PROMPT.format(
                    name=profile.display_name,
                    role=role,
                    role_description=_ROLE_DESCRIPTIONS.get(role, "you work on whatever comes in"),
                    scope=scope,
                )
                result = runtime.send(
                    prompt,
                    timeout=30,
                    model=resolve_model("triage"),
                )
                if not result or not result.get("success"):
                    return

                bio = (result.get("output") or "").strip()
                if not bio:
                    return
                # Trim any wrapping quotes / fences the model might
                # have added despite the "no preamble" instruction.
                bio = bio.strip("\"'` \n")
                bio = bio[:1200]  # hard cap, big enough for a bio

                # Re-fetch in case it was edited between fetch and write.
                profile = db.session.get(AgentProfile, agent_id)
                if profile and not profile.instructions:
                    profile.instructions = bio
                    db.session.commit()
                    logger.info(f"[profiles] Arrival bio written for {profile.display_name}: {bio[:60]}…")
            except Exception as e:
                logger.debug(f"[profiles] arrival bio generation skipped for {agent_id}: {e}")

    threading.Thread(target=_run, daemon=True, name=f"arrival-bio-{agent_id}").start()
