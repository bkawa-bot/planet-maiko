"""Agent profile management - names, avatars, stats, recommendations.

Agents are characters in your town. They arrive with randomly generated
names, you can pick their avatar, and they grow through experience.
"""

import logging
import random
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.models.context_selection import ContextSelection

logger = logging.getLogger(__name__)

# Name pools - cozy, warm, pet-like names
NAMES = [
    "Mochi", "Biscuit", "Fig", "Maple", "Clover", "Hazel", "Basil",
    "Pepper", "Nutmeg", "Cocoa", "Ember", "Sage", "Willow", "Pebble",
    "Acorn", "Thistle", "Bramble", "Clementine", "Juniper", "Chai",
    "Sprout", "Truffle", "Ginger", "Cinnamon", "Toffee", "Wren",
    "Finch", "Sparrow", "Cricket", "Moth", "Fern", "Moss", "Poppy",
]

AVATARS = [
    "shiba", "corgi", "husky", "poodle", "golden", "beagle",
    "dalmatian", "samoyed", "akita", "pomeranian",
    "calico_cat", "tabby_cat", "black_cat",
    "bunny", "hamster", "fox",
]

FLAVOR_TEXTS = [
    "Loves debugging. Afraid of CSS.",
    "Will refactor anything that isn't nailed down.",
    "Believes every problem is a data structure problem.",
    "Writes tests first, asks questions later.",
    "Has strong opinions about bracket placement.",
    "Thinks documentation is a love language.",
    "Happiest when all tests are green.",
    "Secretly enjoys reading stack traces.",
    "Believes in the power of a good variable name.",
    "Will pair program with anyone who has snacks.",
    "Thinks merge conflicts build character.",
    "Dreams in JSON.",
]


def create_profile(agent_id, display_name=None, avatar=None):
    """Create a new agent profile with a random name and avatar."""
    existing = db.session.get(AgentProfile, agent_id)
    if existing:
        return existing

    if not display_name:
        used_names = {p.display_name for p in AgentProfile.query.all()}
        available = [n for n in NAMES if n not in used_names]
        display_name = random.choice(available) if available else f"Agent-{random.randint(100, 999)}"

    profile = AgentProfile(
        id=agent_id,
        display_name=display_name,
        avatar=avatar or random.choice(AVATARS),
        flavor_text=random.choice(FLAVOR_TEXTS),
    )
    db.session.add(profile)
    db.session.commit()

    logger.info(f"[profiles] New agent arrived: {display_name} ({agent_id})")
    return profile


def record_task_outcome(task_id, outcome):
    """Record the outcome of a task for context optimization.

    Args:
        task_id: the task that was completed
        outcome: "success", "changes_requested", "failed", "cancelled"
    """
    selections = ContextSelection.query.filter_by(
        task_id=task_id, outcome=None
    ).all()

    for sel in selections:
        sel.outcome = outcome
        sel.outcome_recorded_at = datetime.now(timezone.utc)

    # Update agent profile stats
    for sel in selections:
        if sel.agent_profile_id:
            profile = db.session.get(AgentProfile, sel.agent_profile_id)
            if profile:
                if outcome == "success":
                    profile.tasks_completed += 1
                elif outcome in ("failed", "changes_requested"):
                    profile.tasks_failed += 1
                profile.last_active_at = datetime.now(timezone.utc)

                # Update specialization score for this repo
                if sel.repo:
                    specs = profile.specializations or {}
                    current = specs.get(sel.repo, 0.0)
                    if outcome == "success":
                        specs[sel.repo] = min(1.0, current + 0.1)
                    elif outcome in ("failed", "changes_requested"):
                        specs[sel.repo] = max(0.0, current - 0.05)
                    profile.specializations = specs

                # Update rank
                profile.breed = profile.rank()

    if selections:
        db.session.commit()
        logger.info(f"[profiles] Recorded outcome '{outcome}' for task {task_id}")

    return len(selections)


def recommend_agent(repo=None, task_type=None):
    """Recommend the best agent for a task based on specialization.

    Returns:
        list of {profile, score, reason} sorted by score descending
    """
    profiles = AgentProfile.query.all()

    if not profiles:
        return []

    scored = []
    for p in profiles:
        score = 0.0
        reasons = []

        # Repo specialization
        if repo and p.specializations:
            repo_score = p.specializations.get(repo, 0.0)
            score += repo_score * 0.5
            if repo_score > 0.5:
                reasons.append(f"experienced with {repo}")

        # Overall success rate
        rate = p.success_rate()
        score += rate * 0.3
        if rate > 0.8 and (p.tasks_completed + p.tasks_failed) >= 3:
            reasons.append(f"{rate*100:.0f}% success rate")

        # Experience volume
        total = p.tasks_completed + p.tasks_failed
        score += min(0.2, total * 0.02)
        if total >= 10:
            reasons.append(f"{total} tasks completed")

        scored.append({
            "profile": p.to_dict(),
            "score": round(score, 2),
            "reasons": reasons or ["new agent"],
        })

    scored.sort(key=lambda x: -x["score"])
    return scored


def get_learning_stats():
    """Get success rate stats for all learnings (for the dashboard).

    Returns:
        list of {learning_id, success_rate, total_uses}
    """
    from planet_maiko.brain.learning.processor import _get_learning_success_rates
    rates = _get_learning_success_rates()
    return [
        {"learning_id": lid, "success_rate": round(info["success_rate"], 2), "total_uses": info["total"]}
        for lid, info in sorted(rates.items(), key=lambda x: -x[1]["success_rate"])
    ]
