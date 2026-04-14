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
    return profile


def judge_outcome(task_id, initial_summary=None, final_summary=None):
    """Use LLM to judge task outcome quality and extract specific categories.

    Returns: dict with {quality_score: 1-10, categories: [str], reasoning: str}
    """
    if not initial_summary or not final_summary:
        return None

    try:
        from planet_maiko.agents.brain_session import BrainSession
        session = BrainSession()
        if not session.runtime or not session.runtime.is_available():
            return None

        prompt = (
            "Compare the initial and final state of this coding task. "
            "Rate the quality 1-10 and list which categories of changes were made.\n\n"
            "Categories: null_safety, error_handling, testing, api_design, architecture, "
            "security, performance, style, naming, docs, pattern\n\n"
            f"Initial:\n{initial_summary[:2000]}\n\n"
            f"Final:\n{final_summary[:2000]}\n\n"
            "Respond in JSON: {\"quality\": N, \"categories\": [...], \"reasoning\": \"...\"}"
        )

        result = session.runtime.send_json(prompt, timeout=30)
        if result:
            return result
    except Exception as e:
        logger.warning(f"[profiles] LLM judge failed for {task_id}: {e}")

    return None


def record_task_outcome(task_id, outcome, initial_summary=None, final_summary=None):
    """Record the outcome of a task for context optimization.

    Args:
        task_id: the task that was completed
        outcome: "success", "changes_requested", "failed", "canceled"
        initial_summary: optional summary of the task before the agent worked on it
        final_summary: optional summary of the task after the agent finished
    """
    # If summaries are provided, try LLM judge for category extraction
    judge_result = None
    if initial_summary and final_summary:
        judge_result = judge_outcome(task_id, initial_summary, final_summary)

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

                # Use judge categories if available, otherwise infer from learning_ids
                if judge_result and judge_result.get("categories") and sel.repo:
                    specs = dict(profile.specializations or {})  # copy to trigger SQLAlchemy dirty
                    for cat in judge_result["categories"]:
                        spec_key = f"{sel.repo}:{cat}"
                        current = specs.get(spec_key, 0.0)
                        if outcome == "success":
                            specs[spec_key] = min(1.0, current + 0.1)
                        elif outcome in ("failed", "changes_requested"):
                            specs[spec_key] = max(0.0, current - 0.05)
                    profile.specializations = specs
                elif sel.repo and sel.learning_ids:
                    specs = dict(profile.specializations or {})  # copy to trigger SQLAlchemy dirty
                    from planet_maiko.models.learning import Learning
                    used_learnings = Learning.query.filter(Learning.id.in_(sel.learning_ids)).all()
                    categories = set(l.category for l in used_learnings)

                    for cat in categories:
                        spec_key = f"{sel.repo}:{cat}"
                        current = specs.get(spec_key, 0.0)
                        if outcome == "success":
                            specs[spec_key] = min(1.0, current + 0.1)
                        elif outcome in ("failed", "changes_requested"):
                            specs[spec_key] = max(0.0, current - 0.05)
                    profile.specializations = specs

                # --- Lens updates: overrides, gaps, territory ---
                if outcome in ("changes_requested", "failed") and sel.learning_ids:
                    lens = dict(profile.lens or {})
                    overrides = list(lens.get("overrides", []))

                    # Check if any feedback signals contradict injected learnings
                    from planet_maiko.models.signal import Signal
                    recent_signals = Signal.query.filter(
                        Signal.source_type == "session_feedback",
                        Signal.created_at >= sel.created_at,
                    ).all()
                    feedback_categories = {s.category for s in recent_signals}

                    from planet_maiko.models.learning import Learning as LensLearning
                    injected = LensLearning.query.filter(LensLearning.id.in_(sel.learning_ids)).all()
                    for l in injected:
                        if l.category in feedback_categories and l.id not in overrides:
                            overrides.append(l.id)

                    lens["overrides"] = overrides

                    # Gap detection: feedback categories with no matching injected learning
                    if outcome == "failed":
                        gaps = list(lens.get("gaps", []))
                        injected_categories = {l.category for l in injected}
                        for cat in feedback_categories:
                            if cat not in injected_categories:
                                gap_entry = {"category": cat, "repo": sel.repo or "", "reason": f"No learning for {cat} in task {task_id}"}
                                if gap_entry not in gaps:
                                    gaps.append(gap_entry)
                        lens["gaps"] = gaps

                    profile.lens = lens

                # Territory tracking
                if sel.repo:
                    lens = dict(profile.lens or {})
                    territory = dict(lens.get("territory", {}))
                    repo_territory = dict(territory.get(sel.repo, {}))
                    repo_territory["_total"] = repo_territory.get("_total", 0) + 1
                    territory[sel.repo] = repo_territory
                    lens["territory"] = territory
                    profile.lens = lens

    if selections:
        db.session.commit()
        logger.info(f"[profiles] Recorded outcome '{outcome}' for task {task_id}")

    return len(selections)


def record_session_feedback(task_id, feedback_category, severity="suggestion"):
    """Record mid-session feedback. Small penalty without closing task."""
    selections = ContextSelection.query.filter_by(task_id=task_id, outcome=None).all()

    penalty_map = {"blocking": -0.05, "warning": -0.02, "suggestion": -0.01}
    penalty = penalty_map.get(severity, -0.01)

    for sel in selections:
        if sel.agent_profile_id:
            profile = db.session.get(AgentProfile, sel.agent_profile_id)
            if profile and sel.repo:
                specs = dict(profile.specializations or {})  # copy to trigger SQLAlchemy dirty
                spec_key = f"{sel.repo}:{feedback_category}"
                current = specs.get(spec_key, 0.0)
                specs[spec_key] = max(0.0, current + penalty)
                profile.specializations = specs

    if selections:
        db.session.commit()
        logger.info(f"[feedback] {feedback_category}/{severity} for task {task_id}")

    return len(selections)


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
