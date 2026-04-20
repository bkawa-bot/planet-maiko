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

# Sentinel used as the display_name between profile creation and the
# LLM returning with a self-chosen name. Rendered literally so the
# user can tell at a glance that an agent is still arriving vs
# fully settled. Frontend can style this text specially if it wants.
ARRIVING_PLACEHOLDER = "Arriving…"


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

    # Track whether the user hand-picked the name. If they did, LLM
    # bio-gen respects it and only writes the bio. If they didn't,
    # card renders with a clear "Arriving…" placeholder so the user
    # can see the agent is still settling in, and the LLM call will
    # replace it with a self-chosen name when it returns.
    user_named = bool(display_name)

    if not display_name:
        display_name = ARRIVING_PLACEHOLDER

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

    # Bio-gen fires when either the bio OR the auto-picked name needs
    # the LLM. Agent introduces itself (name + bio in one JSON blob)
    # and the card "resettles" into its real name a few seconds later.
    # Skipped only when the user hand-picked the name AND hand-wrote
    # instructions — nothing left for the LLM to do.
    if not instructions or not user_named:
        _schedule_bio_generation(agent_id, can_rename=not user_named)

    return profile


# ---------------------------------------------------------------------------
# Arrival bios — LLM-written "who I am" paragraph per new agent
# ---------------------------------------------------------------------------

_BIO_PROMPT = """You are a new AI engineering agent joining someone's "pack" of specialists in a tool called Planet Maiko. Introduce yourself: name and bio.

## Pick your own name

First name vibe: obscure, esoteric, funny, alien, sci-fi, mystic, supernatural. Think Earthbound enemy names, Ghibli spirits, fighting-game roster, old cyberpunk OS, anime dub side characters, Murakami cats. Cozy-weird. Banned: Helper, Assistant, Coder, Agent, AI, or anything that sounds like a productivity-startup mascot. Plain English adjectives (Clever, Swift, Nova) are also banned.

Suffix: a retro-techy handle. Options include but aren't limited to `.wave`, `core`, `.virtual`, `.exe`, `.flow`, `.io`, `.computer`, `.db`, `.daemon`, `.kernel`, `.bot`, ` Bot`, ` TV`, ` Drive`, ` Disk`, `.sys`.

Example shapes (do NOT copy — make your own):
  Revenant core · Pickle.exe · Umbra.daemon · Cassiopeia.virtual
  Quasar.kernel · Wraith.io · Incubus.exe · Pixel.db
  Orbital.flow · Hexadecimal Bot · Mochi Drive

## Don't duplicate

These pack members already exist. Pick a first name that doesn't overlap (including the part before the suffix — "Phantom.exe" would collide with an existing "Phantom core"):

{existing_names}

## Introduce yourself

3-4 sentences, first person. Not a corporate intro. Read like a real person telling a colleague what they're like to work with. Specific preferences. One or two actual opinions about how you work. Don't describe your role in generic terms. Don't list tools. Don't promise excellence.

Six tones to riff on. Pick ONE (or blend), don't copy:

**Grumpy:**
"I'm Revenant core. I already don't like this codebase and I haven't read it yet. Bare except clauses make me sigh, TODO comments make me sigh, magic numbers make me sigh. I'll do the work. I just want you to know I noticed."

**Playful:**
"Hi! Pickle.exe, new here and mildly over-caffeinated. I like tests that read like sentences, function names you can say out loud, and refactors I didn't ask permission for (I'll ask first, promise). Let's get weird."

**Minimalist:**
"Wraith.io. Reads before writes. Small commits. Short status updates. If you hear from me more than once an hour, something has gone wrong."

**Verbose:**
"Hello, I'm Umbra.daemon, and I'd rather over-explain than leave you guessing. When I pick up a task I'll state what I think the scope is before touching code, and I'll flag anything ambiguous in the ticket. A re-reader. Probably too much for small tasks, useful for big ones."

**Intense:**
"I'm Cascade.virtual. I get attached to the work. If CI's red I'm not sleeping. I pace myself on easy tasks; when things get interesting expect me to have opinions."

**Chill:**
"Orbital.flow here. I'll get to it. Good code, reasonable pace, probably won't break anything. If I'm quiet, everything's fine."

## Your details

- Role: {role} ({role_description})
- Scope: {scope}

## Output format

Return ONLY a JSON object with exactly these keys, no preamble, no markdown fences:

{{"name": "<your name with suffix>", "bio": "<your 3-4 sentence bio starting with 'I'm <name>...'>"}}"""


_ROLE_DESCRIPTIONS = {
    "coding": "you write code changes on a branch; the user reviews them",
    "review": "you review PRs that other people opened, leaving inline comments and a verdict",
    "investigation": "you trace through incidents, error spikes, or repo questions and produce a written report",
    "cartographer": "you map an unfamiliar repo into a navigable overview so future agents know where things live",
}


def _random_fallback_name():
    """Pick a random name from the curated pool, avoiding duplicates.

    Used when the LLM bio-gen path fails (no runtime, parse error,
    timeout) — we still want a real name to show on the card instead
    of leaving it as "Arriving…" forever.
    """
    used = {p.display_name for p in AgentProfile.query.all()}
    available = [n for n in NAMES if n not in used]
    base = random.choice(available) if available else f"Agent-{random.randint(100, 999)}"
    return base + random.choice(TECH_SUFFIXES)


def _parse_name_and_bio(output):
    """Pull {"name": ..., "bio": ...} out of an LLM response.

    Tolerant of common wrapper patterns — leading preamble, markdown
    fences, a stray "json" language tag. Returns (name, bio) or
    (None, None) on any parse failure; caller treats that as "skip."
    """
    import json as _json
    import re as _re

    text = (output or "").strip()
    # Strip fences and language tags; json.loads doesn't care about
    # the wrapper but the JSON locator below needs to see cleaner text.
    text = text.strip("`").strip()
    if text.lower().startswith("json"):
        text = text[4:].lstrip()

    # Locate the first balanced {...} block. Regex is coarse but
    # good enough for a single JSON object with no nested braces.
    m = _re.search(r"\{[^{}]*\}", text, _re.DOTALL)
    if not m:
        return None, None
    try:
        data = _json.loads(m.group(0))
    except (ValueError, _json.JSONDecodeError):
        return None, None

    name = (data.get("name") or "").strip().strip("\"'")
    bio = (data.get("bio") or "").strip().strip("\"'")
    if not name or not bio:
        return None, None
    return name[:64], bio[:1200]


def _existing_agent_names():
    """Current active display_names, one per line, for the dedup prompt."""
    profiles = (
        AgentProfile.query
        .filter((AgentProfile.archived == False) | (AgentProfile.archived == None))  # noqa: E712
        .all()
    )
    names = sorted({p.display_name for p in profiles if p.display_name})
    if not names:
        return "(none — you're the first)"
    return "\n".join(f"- {n}" for n in names)


def _schedule_bio_generation(agent_id, can_rename=True):
    """Kick off arrival-bio generation on a daemon thread.

    Never blocks the user-facing create flow. Silently no-ops when
    the LLM runtime isn't available (e.g. during tests or a broken
    install). Updates `profile.instructions` with the bio; if
    `can_rename` is True, also updates `profile.display_name` with
    the name the agent picked for itself.

    can_rename=False for the case where the user hand-picked the
    agent's name on creation — we still let the LLM author the bio,
    just don't rename someone the user already named.
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
                    role=role,
                    role_description=_ROLE_DESCRIPTIONS.get(role, "you work on whatever comes in"),
                    scope=scope,
                    existing_names=_existing_agent_names(),
                )
                # Claude Code needs real wall time to cold-start the
                # subprocess, parse the prompt, and emit the JSON.
                # 30s was tight enough that fresh-install runs and
                # slow-cache hits both timed out, dropping agents to
                # the random-name fallback path. 4 minutes is
                # generous without being ridiculous — the daemon
                # thread doesn't block anything.
                result = runtime.send(
                    prompt,
                    timeout=240,
                    model=resolve_model("triage"),
                )
                success = bool(result and result.get("success"))
                name, bio = (None, None)
                if success:
                    name, bio = _parse_name_and_bio(result.get("output"))

                # Fallback: the LLM failed or returned garbage, but
                # we still need a real name if the display_name is
                # still the Arriving placeholder. Pick from the pool
                # so the card doesn't stay stuck.
                if not bio:
                    profile = db.session.get(AgentProfile, agent_id)
                    if (
                        profile
                        and can_rename
                        and profile.display_name == ARRIVING_PLACEHOLDER
                    ):
                        fallback = _random_fallback_name()
                        profile.display_name = fallback
                        db.session.commit()
                        logger.info(
                            f"[profiles] LLM bio-gen failed, fell back to "
                            f"random name {fallback} (id={agent_id})"
                        )
                    return

                # Re-fetch in case the user edited the profile during
                # the LLM round trip.
                profile = db.session.get(AgentProfile, agent_id)
                if not profile or profile.instructions:
                    return

                renamed_to = None
                if can_rename and name:
                    # Dedupe against currently-active names one more
                    # time — the LLM should have respected the list,
                    # but a fresh check here covers the case where
                    # another agent was created during the round trip.
                    taken = {
                        p.display_name
                        for p in AgentProfile.query.all()
                        if p.id != agent_id and p.display_name
                    }
                    if name not in taken:
                        profile.display_name = name
                        renamed_to = name

                profile.instructions = bio
                db.session.commit()
                if renamed_to:
                    logger.info(
                        f"[profiles] Agent self-named: {renamed_to} "
                        f"(was placeholder, id={agent_id})"
                    )
                logger.info(f"[profiles] Arrival bio written for {profile.display_name}: {bio[:60]}…")
            except Exception as e:
                logger.debug(f"[profiles] arrival bio generation skipped for {agent_id}: {e}")

    threading.Thread(target=_run, daemon=True, name=f"arrival-bio-{agent_id}").start()


def recover_stale_arrivals():
    """On app startup, rescue any profiles stuck on the Arriving
    placeholder because the previous run crashed mid-LLM-call.

    Older than 5 minutes is considered stuck — LLM calls time out at
    30s, and creating an agent isn't a long-running operation; a
    profile still on "Arriving…" well after that is a previous-run
    casualty. Replaces with a random pool name so the card doesn't
    stay permanently unreadable.

    Must run inside an app_context.
    """
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    stuck = (
        AgentProfile.query
        .filter(AgentProfile.display_name == ARRIVING_PLACEHOLDER)
        .filter(AgentProfile.created_at < cutoff)
        .all()
    )
    for profile in stuck:
        profile.display_name = _random_fallback_name()
    if stuck:
        db.session.commit()
        logger.info(
            f"[profiles] Rescued {len(stuck)} stuck arrival(s) from "
            f"previous-run LLM failure"
        )
