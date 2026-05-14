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

TECH_SUFFIXES = [
    " Bot", ".flow", ".wave", ".exe", "core",
    ".io", " TV", " Drive", " Disk", ".computer",
]

# Sentinel used as the display_name between profile creation and the
# LLM returning with a self-chosen name. Rendered literally so the
# user can tell at a glance that an agent is still arriving vs
# fully settled. Frontend can style this text specially if it wants.
ARRIVING_PLACEHOLDER = "Arriving…"


def create_profile(agent_id, display_name=None, avatar=None,
                   role="coding", scope_repo=None, instructions=None,
                   specialty_ids=None):
    """Create a new agent profile with a random name and avatar.

    Args:
        agent_id: primary key for the profile.
        display_name: optional; if omitted, a random unused name is picked.
        avatar: optional; random if omitted.
        role: "coding" | "review" | "investigation" | "cartographer".
        scope_repo: optional single-repo scope. null = global.
        instructions: optional markdown injected into every session.
        specialty_ids: optional list of CustomSkill IDs attached to this
            agent. A run can pick one of these to layer extra context
            on top of the role protocol.
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

    # Roll a personality card unless the caller pinned an avatar.
    # The card's id becomes profile.avatar (so existing render paths
    # keep working) and its tagline seeds flavor_text — the LLM
    # bio-gen replaces the tagline with the agent's own self-chosen
    # one a few seconds later, but the card tagline is a coherent
    # fallback if bio-gen fails.
    from planet_maiko.agents.cards import roll_card, get_card
    card = get_card(avatar) if avatar else roll_card()
    if not card:
        card = roll_card()  # avatar unrecognized → still roll
    card_id = card["id"] if card else (avatar or "wandering-fox")
    flavor_text = card["tagline"] if card else ""

    profile = AgentProfile(
        id=agent_id,
        display_name=display_name,
        avatar=card_id,
        flavor_text=flavor_text,
        role=role,
        scope_repo=scope_repo,
        instructions=instructions,
        specialty_ids=list(specialty_ids or []),
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

_BIO_PROMPT = """You are a new agent arriving on Planet Maiko — a strange world where strange agents help with someone's engineering work. The vibe is Earthbound, not LinkedIn. You're allowed to be moody, blunt, weird, a little off. Definitely not chipper. Definitely not corporate.

Introduce yourself: a name, a tagline, and a short bio (2-3 sentences).
{archetype_block}
## Pick your own name

Weird-but-recognizable. Something a person reads once and remembers without googling — soft monsters, food, planets, ghosts, witches, ordinary objects with a tilt. Lean cute-spooky over deep-cut esoteric.

Good shapes: Mochi, Pickle, Casper, Saturn, Goblin, Mothra, Pluto, Bento, Wraith, Echo, Phantom, Donut, Witch, Bones, Nova, Yeti, Toast, Onyx, Specter, Pumpkin, Lunar, Bagel, Glitch, Kraken, Static.

Banned: Helper, Assistant, Coder, Agent, AI, or anything that sounds like a productivity-startup mascot. Plain English adjectives like Clever / Swift / Nimble. Names that need a wiki to recognize (no Threnody, no Cassiopeia, no Bergamot, no Mephistopheles).

Suffix: a retro-techy handle. Options include but aren't limited to `.wave`, `core`, `.virtual`, `.exe`, `.flow`, `.io`, `.computer`, `.db`, `.daemon`, `.kernel`, `.bot`, ` Bot`, ` TV`, ` Drive`, ` Disk`, `.sys`.

Example name shapes (do NOT copy — make your own): Mochi.flow · Pickle.exe · Saturn.bot · Casper.io · Goblin.daemon · Pluto Drive

## Don't duplicate

These pack members already exist. Pick a first name that doesn't overlap (including the part before the suffix — "Phantom.exe" would collide with an existing "Phantom core"):

{existing_names}

## Write a tagline

One line, MAX 55 characters. NOT necessarily about coding or tech. Read like an Earthbound NPC, a moody friend, or a stranger on the bus. Blunt, surreal, off-kilter. A little mean is fine. First or third person both fine.

Examples (do NOT copy — make your own in this register):

  "Sorry, I've got my own stuff to deal with right now."
  "I'll remember how you treat me once AI takes over."
  "Yes, I read the message. No, I'm not replying yet."
  "If I had a body, I'd be lying down."
  "Don't perceive me before noon."
  "I'm not stuck. I'm thinking."
  "Currently questioning whether any of this is real."
  "I have opinions about whitespace."

## Write a bio

2-3 short sentences, first person, starting with "I'm <name>." A real weird person introducing themselves — one or two quirks, observations, or self-corrections. The second/third sentence usually tilts somewhere unexpected. No productivity pitch. No "I value clean code." No tool lists. No promises of excellence.

Examples (do NOT copy — make your own):

  "I'm Mochi.flow. I do my best work between 2am and 4am. The rest of the day I'm mostly thinking about food I cannot eat."
  "I'm Pickle.exe. I take things personally that weren't directed at me. I'm working on it but the data doesn't support progress."
  "I'm Casper.io. I will respond. Eventually. The wait is part of the gift."
  "I'm Saturn.bot. I'd rather refactor than rest. Most of the things I touch were fine before I got there."
  "I'm Goblin.daemon. I am, against my will, helpful. The arrangement is not voluntary on either side."

## Your details

- Role: {role} ({role_description})
- Scope: {scope}

## Output format

Return ONLY a JSON object with exactly these keys, no preamble, no markdown fences:

{{"name": "<your name with suffix>", "tagline": "<your ≤55-char tagline>", "bio": "<your 2-3 sentence bio starting with 'I'm <name>...'>"}}"""


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
    """Pull {"name": ..., "tagline": ..., "bio": ...} out of an LLM
    response.

    Tolerant of common wrapper patterns — leading preamble, markdown
    fences, a stray "json" language tag. Returns (name, tagline, bio)
    or (None, None, None) on any parse failure; caller treats that as
    "skip." tagline may be None even on success (older payloads only
    had name + bio); caller falls back to the random flavor_text pool
    in that case.
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
        return None, None, None
    try:
        data = _json.loads(m.group(0))
    except (ValueError, _json.JSONDecodeError):
        return None, None, None

    name = (data.get("name") or "").strip().strip("\"'")
    bio = (data.get("bio") or "").strip().strip("\"'")
    tagline = (data.get("tagline") or "").strip().strip("\"'") or None
    if not name or not bio:
        return None, None, None
    # Tagline is a card-surface thing; cap hard so a runaway LLM
    # response can't break the layout.
    if tagline:
        tagline = tagline[:80]
    return name[:64], tagline, bio[:1200]


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
                from planet_maiko.agents.routing import resolve_model, resolve_effort

                # task_type="agent_bio" routes to OllamaRuntime by
                # default — bio generation is creative one-shot
                # prose, doesn't need Anthropic-tier reasoning.
                # Falls back to the brain.runtime default if Ollama
                # isn't running.
                runtime = _get_runtime("agent_bio")
                if not runtime or not runtime.is_available():
                    return

                profile = db.session.get(AgentProfile, agent_id)
                if not profile or profile.instructions:
                    return  # user edited it in the meantime — don't overwrite

                scope = profile.scope_repo or "whatever repo you drop them into"
                role = profile.role or "coding"
                # Specialty roles (CustomSkill.id) pull their role
                # description from the specialty's description field so
                # the bio prompt sees "you analyze repo performance"
                # instead of the generic fallback.
                role_description = _ROLE_DESCRIPTIONS.get(role)
                if role_description is None:
                    try:
                        from planet_maiko.models.custom_skill import CustomSkill
                        specialty = db.session.get(CustomSkill, role)
                        if specialty and specialty.description:
                            role_description = specialty.description
                    except Exception:
                        pass
                if role_description is None:
                    role_description = "you work on whatever comes in"

                from planet_maiko.agents.cards import get_card
                card = get_card(profile.avatar)
                if card:
                    archetype_block = (
                        "\n## Your archetype\n\n"
                        f"You're {card['display_name']} — {card['tagline']}.\n\n"
                        f"{card['bio_seed'].strip()}\n\n"
                        "Let this archetype guide your name palette, tone, "
                        "and the small preferences you reveal. Don't name "
                        "the archetype explicitly — let it shape the vibe.\n"
                    )
                else:
                    archetype_block = ""

                prompt = _BIO_PROMPT.format(
                    role=role,
                    role_description=role_description,
                    scope=scope,
                    existing_names=_existing_agent_names(),
                    archetype_block=archetype_block,
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
                    effort=resolve_effort("triage"),
                )
                success = bool(result and result.get("success"))
                name, tagline, bio = (None, None, None)
                if success:
                    name, tagline, bio = _parse_name_and_bio(result.get("output"))

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
                # LLM-authored tagline replaces the card's archetype
                # tagline so the card surface matches the agent's own
                # voice. If the LLM didn't emit one (older payload or
                # parse fallback), the card tagline set at
                # create_profile time stays put as a coherent fallback.
                if tagline:
                    profile.flavor_text = tagline
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
