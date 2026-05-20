#!/usr/bin/env python3
"""Seed a sandboxed Planet Maiko instance with believable demo data.

Why this exists: the README/marketing screenshots want a populated,
good-looking Maiko, but you should never screenshot your real work
data. This script builds a SEPARATE sandbox (its own database and
config) and fills it with on-theme made-up data, so nothing real ever
ends up in a screenshot.

Usage:

    python scripts/seed_demo.py              # seed the sandbox
    python scripts/seed_demo.py --wipe       # delete sandbox + reseed
    python scripts/seed_demo.py --sandbox /tmp/maiko-demo

Then launch Maiko against the sandbox (the script prints the exact
command, for bash and PowerShell). The sandbox lives at
~/.maiko-demo by default and is completely independent of your normal
~/.local/share/planet-maiko data.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone


def _utc(days_ago=0, hours_ago=0, minutes_ago=0):
    return datetime.now(timezone.utc) - timedelta(
        days=days_ago, hours=hours_ago, minutes=minutes_ago
    )


# --- the pack ---------------------------------------------------------
# (agent_id, display_name, avatar card id, role, state, stats)
# Names are warm and a little strange, never corporate. Avatars are
# real card ids from data/cards/cards.yaml.
AGENTS = [
    ("demo-pup-biolumen", "Mothball", "biolumen", "coding", "working",
     dict(tasks_completed=14, prs_merged=11, learnings_contributed=6)),
    ("demo-pup-sludge", "Pudding", "sludge-pup", "review", "idle",
     dict(tasks_completed=22, prs_merged=0, learnings_contributed=9,
          prs_changes_requested=18)),
    ("demo-pup-lantern", "Wick", "lantern-pup", "investigation", "idle",
     dict(tasks_completed=8, prs_merged=2, learnings_contributed=4)),
    ("demo-pup-mirror", "Static", "mirror-dog", "coding", "stuck",
     dict(tasks_completed=5, prs_merged=4, learnings_contributed=1)),
    ("demo-pup-glass", "Tincture", "glass-fox", "cartographer", "idle",
     dict(tasks_completed=3, prs_merged=0, learnings_contributed=2)),
]

REPOS = ["aurora-labs/star-charts", "aurora-labs/tide-engine", "moss/garden-server"]

# (id, title, type, status, priority, agent_id or None, days_ago)
TASKS = [
    ("demo-task-1", "Make the star-chart cache stop forgetting things mid-scroll",
     "bug", "in_progress", "high", "demo-pup-biolumen", 0),
    ("demo-task-2", "Review PR #214: lazy-load the constellation tiles",
     "review", "review", "high", "demo-pup-sludge", 0),
    ("demo-task-3", "The tide-engine faints under 2GB RAM, give it a couch",
     "bug", "blocked", "urgent", "demo-pup-mirror", 1),
    ("demo-task-4", "Port garden/seed.py off the legacy Query API",
     "feature", "new", "normal", None, 2),
    ("demo-task-5", "Figure out why the moss server only flakes on Tuesdays",
     "investigation", "in_progress", "normal", "demo-pup-lantern", 1),
    ("demo-task-6", "Map the star-charts repo so newcomers stop getting lost",
     "repo_analysis", "done", "normal", "demo-pup-glass", 3),
    ("demo-task-7", "Retry backoff for the tide-engine websocket",
     "feature", "done", "normal", "demo-pup-biolumen", 4),
    ("demo-task-8", "Tidy the garden-server import order",
     "todo", "new", "low", None, 5),
]

# (source_id, type, title, body, priority, actionable, action_hint, days/hrs ago)
PUPDATES = [
    ("star-charts#214", "pr_review_requested",
     "Review requested: lazy-load the constellation tiles",
     "quill requested your review on aurora-labs/star-charts#214",
     "high", True, "Review PR", dict(hours_ago=2)),
    ("tide-engine#88", "pr_changes_requested",
     "Changes requested: websocket retry backoff",
     "juniper left 3 comments on aurora-labs/tide-engine#88",
     "high", True, "Address feedback", dict(hours_ago=5)),
    ("star-charts#207", "pr_ci_failed",
     "CI failing: constellation tile cache",
     "Failed checks on aurora-labs/star-charts#207: unit, typecheck",
     "high", True, "Fix CI", dict(hours_ago=7)),
    ("MOSS-141", "linear_assigned",
     "MOSS-141: garden-server cold start is 40s",
     "Assigned to you. Cold start walks every seed file synchronously.",
     "normal", True, "Create task", dict(days_ago=1)),
    ("MOSS-139", "linear_mention",
     "sable mentioned you in MOSS-139",
     "sable: can you sanity-check the migration order here before I merge?",
     "high", True, "Open in Linear", dict(days_ago=1, hours_ago=3)),
    ("tide-engine#84", "pr_merged",
     "PR merged: websocket retry backoff",
     "fenn merged aurora-labs/tide-engine#84",
     "low", False, None, dict(days_ago=2)),
]

# Rulebook: signals (raw reviewer quotes) clustered into learnings (rules).
# Each learning carries a list of `quotes` (the original PR-review
# comments) so when the user expands a rule in the Knowledge UI they
# see varied reviewer voices, not the rule text repeated N times.
# (rule, category, repo, signal_count, confidence, is_global,
#  violation_description, quotes)
LEARNINGS = [
    ("Add new nullable columns to the idempotent patch list, never a "
     "hard migration: existing SQLite databases must survive an update.",
     "architecture", "aurora-labs/star-charts", 4, 0.92, True,
     "A schema change introduces a non-additive migration or a NOT NULL "
     "column without a default, which breaks existing local databases.",
     [
       "this needs to be in the patch list or older databases will break on update",
       "ALTER TABLE goes through _ensure_new_columns, not a hard migration here",
       "had to roll back v0.3.2 because this added a NOT NULL column with no default",
       "let's keep migrations idempotent. if i run it twice it should be a no-op",
     ]),
    ("Pass task_type through to the runtime resolver. If a call site uses "
     "resolve_model(X) the matching runtime must use the same key or "
     "per-task routing silently no-ops.",
     "gotcha", "aurora-labs/tide-engine", 3, 0.88, False,
     "A model-resolution call site and its runtime use different task_type "
     "keys, so the per-task routing rule never takes effect.",
     [
       "resolve_model('cartograph') here but _get_runtime() is called without task_type, so the routing rule never fires",
       "spent 20 min debugging why per-task routing wasn't working. it's this.",
       "if you add a resolve_model() call site you have to wire task_type through, or the rule silently no-ops",
     ]),
    ("Prefer select(...) over the legacy Query API in new code; the old "
     "path is being removed and mixing them breaks eager loading.",
     "style", "moss/garden-server", 5, 0.81, True,
     "New code uses the legacy Query API instead of select(...).",
     [
       "use select() here, Query is being removed",
       "mixing eager-load styles between Query and select breaks the join in subtle ways. we caught one in #84",
       "new code goes through 2.0 style, see the migration note in CONTRIBUTING",
       "the old way works, but every new file that uses it adds to the cleanup pile",
       "switch to select(Foo).where(...) please",
     ]),
    ("Null-check the cursor before .fetchone(); the moss driver returns "
     "None on an empty result instead of raising.",
     "null_safety", "moss/garden-server", 2, 0.74, False,
     "A .fetchone() result is used without checking it against None.",
     [
       "cursor.fetchone() returns None when there are no rows here. needs a guard",
       "this crashed in prod on an empty result set. please null-check before .get('id')",
     ]),
    ("Commit inside the daemon thread that did the work. A worker that "
     "writes rows but never commits loses everything on thread exit.",
     "gotcha", "aurora-labs/tide-engine", 3, 0.86, True,
     "A background thread mutates the session but returns without "
     "committing or rolling back.",
     [
       "the worker thread writes but never commits, so on thread exit the session rolls back and you lose the row",
       "had this exact bug last sprint. need a db.session.commit() at the end of _run.",
       "each thread is its own caller. you commit yourself in the worker.",
     ]),
    ("Floor displayed times to the minute. Seconds in a timestamp read "
     "as machine output, not something a person would write.",
     "style", "aurora-labs/star-charts", 2, 0.7, False,
     "A user-facing timestamp is rendered with seconds.",
     [
       "drop the seconds in that timestamp. reads as machine output.",
       "users don't write times with seconds. it's an instant tell that something is computer-generated.",
     ]),
]

# Pack insights: the campfire confessions. Agents owning their misses.
INSIGHTS = [
    ("I assumed garden/seed.py was idempotent. It is extremely not. "
     "Running it twice double-seeds the fixtures. I have learned humility.",
     "moss/garden-server", ["gotcha", "fixtures"], "demo-pup-lantern"),
    ("The tide-engine only flakes under 2GB of RAM, and CI happened to "
     "give us 1.75GB on Tuesdays. Three runs to notice. Worth writing down.",
     "aurora-labs/tide-engine", ["ci", "flake"], "demo-pup-mirror"),
    ("Nobody told me the constellation tiles are 1-indexed but the cache "
     "keys are 0-indexed. Now you know too.",
     "aurora-labs/star-charts", ["offset", "cache"], "demo-pup-biolumen"),
    ("The star-charts repo has two files both named utils.py and they do "
     "very different things. Mapped it so the next pup does not suffer.",
     "aurora-labs/star-charts", ["tooling", "onboarding"], "demo-pup-glass"),
]


def _seed_config():
    """Give the sandbox a clean, fictional config so the Home greeting,
    repo list, and integrations panels look real without naming anything
    real."""
    from planet_maiko.config import load_config, save_config

    cfg = load_config()
    cfg.setdefault("user", {})
    cfg["user"]["name"] = "Robin"
    cfg.setdefault("github", {})
    cfg["github"]["enabled"] = True
    cfg["github"]["username"] = "robin"
    cfg["github"]["repos"] = list(REPOS)
    save_config(cfg)


def _seed_pack():
    from planet_maiko.agents.profiles import create_profile
    from planet_maiko.database import db
    from planet_maiko.models.agent_profile import AgentProfile

    for agent_id, name, avatar, role, state, stats in AGENTS:
        # Pass instructions so nothing tries to LLM-generate a bio.
        create_profile(
            agent_id,
            display_name=name,
            avatar=avatar,
            role=role,
            instructions=f"You are {name}. Keep it warm and a little strange.",
        )
        prof = db.session.get(AgentProfile, agent_id)
        prof.state = state
        prof.last_active_at = _utc(hours_ago=1 if state == "working" else 20)
        for k, v in stats.items():
            setattr(prof, k, v)
    db.session.commit()


def _seed_tasks():
    from planet_maiko.database import db
    from planet_maiko.models.task import Task

    for tid, title, ttype, status, prio, agent, days in TASKS:
        if db.session.get(Task, tid):
            continue
        db.session.add(Task(
            id=tid, title=title, type=ttype, status=status, priority=prio,
            assigned_agent_id=agent,
            created_at=_utc(days_ago=days + 1),
            updated_at=_utc(days_ago=days),
            tags=["demo"],
        ))
    db.session.commit()


def _seed_pupdates():
    from planet_maiko.database import db
    from planet_maiko.models.pupdate import Pupdate

    for source_id, ptype, title, body, prio, actionable, hint, when in PUPDATES:
        pid = f"demo-{source_id}".replace("/", "-").replace("#", "-")
        if db.session.get(Pupdate, pid):
            continue
        db.session.add(Pupdate(
            id=pid, source="github" if "#" in source_id else "linear",
            source_id=source_id, type=ptype, title=title, body=body,
            priority=prio, actionable=actionable, action_hint=hint,
            timestamp=_utc(**when), tags=["demo"],
        ))
    db.session.commit()


def _seed_memos():
    from planet_maiko.brain.memos import create_memo
    from planet_maiko.database import db

    create_memo(
        kind="agent_ready", category="waiting",
        title="Pudding finished reviewing PR #214",
        body="Left a verdict and 2 inline notes. Wants your eyes before merge.",
        priority="high", source_agent_id="demo-pup-sludge",
        cta_label="Open review", cta_action="review", dedup=False,
    )
    create_memo(
        kind="agent_stuck", category="waiting",
        title="Static is stuck on the tide-engine bug",
        body="Cannot reproduce the 2GB faint locally. Needs a CI box or a "
             "pointer before it keeps guessing.",
        priority="high", source_agent_id="demo-pup-mirror",
        cta_label="Reply", cta_action="open", dedup=False,
    )
    create_memo(
        kind="agent_proposal", category="offer",
        title="Wick proposes splitting the moss flake investigation",
        body="It looks like two bugs wearing one trenchcoat. Wick wants to "
             "fork a second task. Approve?",
        priority="normal", source_agent_id="demo-pup-lantern",
        cta_label="Approve", cta_action="approve", dedup=False,
    )
    create_memo(
        kind="skill_result", category="info",
        title="Repo overview refreshed: aurora-labs/star-charts",
        body="Tincture re-walked the tree. The two utils.py files are now "
             "documented so nobody else trips on them.",
        priority="normal", source_agent_id="demo-pup-glass", dedup=False,
    )
    create_memo(
        kind="notification", category="info",
        title="Nightly: 3 PRs merged, pack is quiet",
        body="Mothball shipped the retry backoff. Nothing is on fire.",
        priority="low", dedup=False,
    )
    db.session.commit()


def _seed_rulebook():
    from planet_maiko.database import db
    from planet_maiko.models.learning import Learning
    from planet_maiko.models.signal import Signal

    reviewers = ["quill", "juniper", "sable", "fenn"]
    for i, (rule, cat, repo, count, conf, is_global, viol, quotes) in enumerate(LEARNINGS):
        if Learning.query.filter_by(rule=rule).first():
            continue
        learning = Learning(
            rule=rule, category=cat, scope_repo=repo, is_global=is_global,
            confidence=conf, signal_count=count, source="auto", status="active",
            last_signal_at=_utc(days_ago=i),
            violation_description=viol,
            violation_description_signal_count=count,
            violation_description_generated_at=_utc(days_ago=i),
        )
        db.session.add(learning)
        db.session.flush()
        # Confirming signals are the original reviewer comments that got
        # clustered into the rule. Different text per signal so expanding
        # a rule in the UI shows a real-feeling chorus of voices.
        for n in range(count):
            quote = quotes[n % len(quotes)]
            db.session.add(Signal(
                category=cat, text=quote, original_text=quote,
                source_type="pr_comment",
                reviewer=reviewers[n % len(reviewers)], severity="suggestion",
                repo=repo, learning_id=learning.id, aggregated=True,
                synthesized=True, created_at=_utc(days_ago=i, hours_ago=n),
            ))
    db.session.commit()


def _seed_insights():
    from planet_maiko.database import db
    from planet_maiko.models.insight import Insight

    for text, repo, tags, author in INSIGHTS:
        if Insight.query.filter_by(text=text).first():
            continue
        db.session.add(Insight(
            text=text, repo_scope=repo, tags=tags, status="active",
            author_agent_id=author, last_confirmed_at=_utc(hours_ago=6),
        ))
    db.session.commit()


def _seed_jobs():
    """An investigation job with a written report so the agent-deliverable
    view has something to show. (A literal code-diff screenshot needs a
    real worktree and cannot be faked here.)"""
    from planet_maiko.database import db
    from planet_maiko.models.agent_job import AgentJob

    if db.session.get(AgentJob, "demo-job-1"):
        return
    db.session.add(AgentJob(
        id="demo-job-1", kind="investigation",
        title="Why does the moss server only flake on Tuesdays?",
        description="Trace the Tuesday flake and report.",
        scope_repo="moss/garden-server", status="done",
        agent_profile_id="demo-pup-lantern", created_by="user",
        artifact=(
            "## Finding\n\nIt is not Tuesday. CI assigns the small runner "
            "(1.75GB) on a weekly rotation that happens to land on Tuesday. "
            "The tide-engine import pre-allocates a 2GB buffer and the OOM "
            "killer takes it quietly, so the test just 'flakes'.\n\n"
            "## Fix\n\nPin the runner size for this job, or lazy-allocate "
            "the buffer. I would do both. The pack agrees."
        ),
        created_at=_utc(days_ago=1), finished_at=_utc(days_ago=1, hours_ago=-1),
    ))
    db.session.commit()


def _seed_automations():
    """The built-in automations. Idempotent; safe to call every run."""
    try:
        from planet_maiko.brain.automations.seeding import (
            ensure_plugin_default_automations,
            ensure_seed_automations,
            ensure_seed_rule_automations,
        )
        ensure_seed_rule_automations()
        ensure_seed_automations()
        ensure_plugin_default_automations()
    except Exception as e:  # noqa: BLE001
        print(f"  (automation seed skipped: {e})")


def _already_seeded():
    from planet_maiko.database import db
    from planet_maiko.models.agent_profile import AgentProfile

    return db.session.get(AgentProfile, "demo-pup-biolumen") is not None


def main():
    parser = argparse.ArgumentParser(description="Seed a sandboxed demo Maiko.")
    parser.add_argument(
        "--sandbox",
        default=os.path.join(os.path.expanduser("~"), ".maiko-demo"),
        help="Sandbox directory (its own DB + config). Default: ~/.maiko-demo",
    )
    parser.add_argument(
        "--wipe", action="store_true",
        help="Delete the sandbox first, then reseed from scratch.",
    )
    args = parser.parse_args()

    sandbox = os.path.abspath(args.sandbox)
    if args.wipe and os.path.isdir(sandbox):
        print(f"Wiping sandbox: {sandbox}")
        shutil.rmtree(sandbox)
    os.makedirs(sandbox, exist_ok=True)

    # Point Maiko's data + config at the sandbox BEFORE importing the app
    # so create_app builds an isolated DB and config. Your real
    # ~/.local/share/planet-maiko is never touched.
    os.environ["MAIKO_DATA_DIR"] = sandbox
    os.environ["MAIKO_CONFIG_DIR"] = sandbox

    # Make `src/` importable when run straight from a clone.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(repo_root, "src")
    if os.path.isdir(src) and src not in sys.path:
        sys.path.insert(0, src)

    from planet_maiko.app import create_app

    app = create_app(start_scheduler=False)
    with app.app_context():
        if _already_seeded():
            print(
                "Sandbox already has demo data. Re-run with --wipe for a "
                "clean slate."
            )
        else:
            print("Seeding demo data into the sandbox...")
            _seed_config()
            _seed_pack()
            _seed_tasks()
            _seed_pupdates()
            _seed_memos()
            _seed_rulebook()
            _seed_insights()
            _seed_jobs()
            _seed_automations()
            print("Done. The pack has arrived.")

    db_hint = os.path.join(sandbox, "maiko.db")
    print()
    print("Launch Maiko against the sandbox (real data stays untouched):")
    print()
    print("  macOS / Linux:")
    print(f'    MAIKO_DATA_DIR="{sandbox}" MAIKO_CONFIG_DIR="{sandbox}" maiko up')
    print()
    print("  Windows PowerShell:")
    print(f'    $env:MAIKO_DATA_DIR="{sandbox}"; '
          f'$env:MAIKO_CONFIG_DIR="{sandbox}"; maiko up')
    print()
    print(f"Sandbox DB: {db_hint}")
    print("Take your screenshots, then delete the sandbox folder when done.")


if __name__ == "__main__":
    main()
