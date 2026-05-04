"""Agent personality cards — archetypes that seed name + bio + avatar.

Cards are STATIC authored content, loaded from
src/planet_maiko/data/cards/cards.yaml. Each card has an `id` (also
stored on AgentProfile.avatar so existing render paths keep working)
and a `bio_seed` injected into the LLM prompt at agent creation.

Rarity drives a weighted random roll: per-rarity tier weights are
configurable via config.agents.cards.weights. Within a tier, cards
are equiprobable — so adding more cards at the same rarity shrinks
each card's odds without affecting other tiers.
"""

import logging
import os
import random
from functools import lru_cache

import yaml

from planet_maiko.database import db
from planet_maiko.models.agent_profile import AgentProfile

logger = logging.getLogger(__name__)


DEFAULT_WEIGHTS = {
    "common": 50,
    "uncommon": 25,
    "rare": 15,
    "epic": 7,
    "legendary": 3,
}


def cards_yaml_path():
    return os.path.join(
        os.path.dirname(__file__), "..", "data", "cards", "cards.yaml"
    )


@lru_cache(maxsize=1)
def load_cards():
    """Load card definitions. Cached for the process lifetime."""
    path = cards_yaml_path()
    if not os.path.isfile(path):
        logger.warning(f"[cards] cards.yaml not found at {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        cards = yaml.safe_load(f) or []
    required = {"id", "display_name", "rarity", "tagline", "bio_seed"}
    valid = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        if not required.issubset(card.keys()):
            logger.warning(f"[cards] dropping malformed card: {card!r}")
            continue
        valid.append(card)
    logger.info(f"[cards] loaded {len(valid)} card archetype(s)")
    return valid


def get_card(card_id):
    """Return the card dict for `card_id`, or None if unknown."""
    if not card_id:
        return None
    for card in load_cards():
        if card["id"] == card_id:
            return card
    return None


def known_card_ids():
    """Set of card ids currently defined. Used by backfill to detect
    legacy `avatar` values (e.g. "shiba") that need re-rolling."""
    return {c["id"] for c in load_cards()}


def _resolve_weights(weights=None):
    if weights:
        merged = dict(DEFAULT_WEIGHTS)
        merged.update(weights)
        return merged
    try:
        from planet_maiko.config import load_config
        configured = (
            load_config().get("agents", {}).get("cards", {}).get("weights", {})
        )
    except Exception:
        configured = {}
    if configured:
        merged = dict(DEFAULT_WEIGHTS)
        merged.update(configured)
        return merged
    return dict(DEFAULT_WEIGHTS)


def roll_card(weights=None):
    """Pick a card via rarity-weighted random.

    Each rarity TIER carries the configured weight; within a tier,
    cards are equiprobable. With defaults
    (common 50 / uncommon 25 / rare 15 / epic 7 / legendary 3), two
    common cards each get 25/100 odds and a sole legendary gets 3/100.

    Returns the card dict, or None when no cards are loaded.
    """
    cards = load_cards()
    if not cards:
        return None
    weight_map = _resolve_weights(weights)
    by_rarity = {}
    for card in cards:
        by_rarity.setdefault(card["rarity"], []).append(card)
    flat_cards = []
    flat_weights = []
    for rarity, group in by_rarity.items():
        tier_weight = weight_map.get(rarity, 0)
        if tier_weight <= 0 or not group:
            continue
        per_card = tier_weight / len(group)
        for card in group:
            flat_cards.append(card)
            flat_weights.append(per_card)
    if not flat_cards:
        # All tier weights zero or unrecognized rarities — fall back
        # to uniform so we never return None when cards exist.
        return random.choice(cards)
    return random.choices(flat_cards, weights=flat_weights, k=1)[0]
