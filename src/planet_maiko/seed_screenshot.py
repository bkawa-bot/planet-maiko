"""Screenshot-demo seed — minimal fixture set for the screenshot
flow (recording marketing visuals, smoke-testing the UI). Lifted out
of seed.py so the bigger seed_data and screenshot demo live in
separate files.
"""

"""Seed the database with realistic test data.

Idempotent: checks for existence of 'agent-mochi' before inserting.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.models.diff_comment import DiffComment
from planet_maiko.models.insight import Insight
from planet_maiko.models.project import Project
from planet_maiko.models.task import Task
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.learning import Learning
from planet_maiko.models.signal import Signal

logger = logging.getLogger(__name__)

_NOW = datetime.now(timezone.utc)





def seed_screenshot_demo(app):
    """Add screenshot-ready demo data. Idempotent — safe to run on top
    of an existing seed or a live DB. Designed to make the Home page,
    Pack Insights ritual, and review surfaces all read well in a capture.

    What this seeds:
      - State on the three seeded agents so their dots are distinct
        (Mochi: working, Biscuit: working, Hazel: stuck)
      - Pack Request pupdates (agent_ready_for_review, agent_plan_for_approval,
        agent_stuck) so PackStatusPane has rows
      - DiffComment entries on task-auth-pkce for the review screenshot
      - AgentMessage entries (feedback + insight) so the Pack Insights
        ritual has something to collect
      - Insights in the playbook
      - A pre-baked home-overview SkillResult so the page renders
        immediately without waiting on the LLM
    """
    with app.app_context():
        logger.info("Seeding screenshot demo data…")
        added = 0

        # --- Agent state (idempotent update) ---
        state_map = {
            "agent-mochi": "working",
            "agent-biscuit": "working",
            "agent-hazel": "stuck",
        }
        for agent_id, state in state_map.items():
            profile = db.session.get(AgentProfile, agent_id)
            if profile and profile.state != state:
                profile.state = state

        # --- Pack Request pupdates ---
        pack_request_pupdates = [
            Pupdate(
                id="pup-demo-plan-ready",
                timestamp=_ago(minutes=14),
                source="maiko",
                source_id="agent:hazel:task-rate-tiers:plan_ready",
                type="agent_plan_for_approval",
                priority="high",
                title="Hazel.virtual has a plan ready",
                body="Rate-limit tier config: she sketched a schema with org-level override + per-key opt-in. Wants a nod before writing.",
                actionable=True,
                action_hint="Review plan",
                tags=["agent-hazel", "task-rate-tiers"],
                extra={"task_id": "task-rate-tiers", "agent_id": "agent-hazel"},
            ),
            Pupdate(
                id="pup-demo-review-ready",
                timestamp=_ago(minutes=34),
                source="maiko",
                source_id="agent:mochi:task-auth-pkce:ready",
                type="agent_ready_for_review",
                priority="high",
                title="Mochi.flow is ready for review",
                body="PKCE flow finished. Verdict: approve_with_comments. Two tiny notes on the retry path.",
                actionable=True,
                action_hint="Review diff",
                tags=["agent-mochi", "task-auth-pkce"],
                extra={"task_id": "task-auth-pkce", "agent_id": "agent-mochi"},
            ),
            Pupdate(
                id="pup-demo-stuck",
                timestamp=_ago(hours=1, minutes=12),
                source="maiko",
                source_id="agent:hazel:task-rate-tiers:stuck",
                type="agent_stuck",
                priority="high",
                title="Hazel.virtual is stuck",
                body="Multi-key orgs. She can't tell if limits should be per-key or aggregated at the org level. Wants a call.",
                actionable=True,
                action_hint="Help out",
                tags=["agent-hazel", "task-rate-tiers"],
                extra={"task_id": "task-rate-tiers", "agent_id": "agent-hazel"},
            ),
        ]
        for p in pack_request_pupdates:
            if not db.session.get(Pupdate, p.id):
                db.session.add(p)
                added += 1

        # --- Diff comments for the review screenshot ---
        pkce_diff_comments = [
            DiffComment(
                task_id="task-auth-pkce",
                file_path="src/auth/pkce.py",
                line_number=47,
                side="new",
                author="agent",
                body="The verifier length check rejects 42 chars here, but the RFC says 43-128. Off-by-one — should be `>= 43`.",
                status="submitted",
                created_at=_ago(minutes=36),
                updated_at=_ago(minutes=36),
            ),
            DiffComment(
                task_id="task-auth-pkce",
                file_path="src/auth/pkce.py",
                line_number=89,
                side="new",
                author="agent",
                body="The retry loop catches `Exception` broadly. The `no-bare-except` rule we landed last sprint applies here — pull out `ConnectionError` and `TimeoutError` explicitly.",
                status="submitted",
                created_at=_ago(minutes=35),
                updated_at=_ago(minutes=35),
            ),
            DiffComment(
                task_id="task-auth-pkce",
                file_path="tests/test_pkce.py",
                line_number=12,
                side="new",
                author="agent",
                body="Nice coverage on the happy path. Consider adding one test for the 128-char verifier boundary.",
                status="submitted",
                created_at=_ago(minutes=34),
                updated_at=_ago(minutes=34),
            ),
        ]
        existing_comments = DiffComment.query.filter_by(task_id="task-auth-pkce").count()
        if existing_comments == 0:
            for c in pkce_diff_comments:
                db.session.add(c)
                added += 1

        # --- Ready-for-review agent message (VERDICT + SUMMARY header) ---
        ready_msg_id = "demo-msg-pkce-ready"
        existing_messages = AgentMessage.query.filter_by(task_id="task-auth-pkce", message_type="ready_for_review").count()
        if existing_messages == 0:
            db.session.add(AgentMessage(
                task_id="task-auth-pkce",
                direction="from_agent",
                sender="Mochi.flow",
                message_type="ready_for_review",
                content=(
                    "VERDICT: approve_with_comments\n"
                    "SUMMARY: PKCE flow is wired end-to-end with the spec-compliant verifier. Two small issues worth addressing before merge: an off-by-one on the verifier length check, and a bare except in the retry loop we've flagged before as a learning.\n\n"
                    "Tests are green locally, coverage held at 84%. The retry-path comment is the one I'd want a second look on — if you want me to tighten it I can push a follow-up in a few minutes."
                ),
                created_at=_ago(minutes=34),
            ))
            added += 1

        # --- AgentMessage entries for the Pack Insights ritual ---
        ritual_messages = [
            AgentMessage(
                task_id="task-auth-pkce",
                direction="from_agent",
                sender="Mochi.flow",
                message_type="feedback",
                content="The `auth_client.retry()` pattern keeps coming up — reviewers flag bare-except on it in three separate PRs this month. Worth graduating the rule.",
                created_at=_ago(hours=2),
            ),
            AgentMessage(
                task_id="task-auth-tests",
                direction="from_agent",
                sender="Biscuit core",
                message_type="feedback",
                content="`assertTrue(x)` is still showing up in new test files even though pytest's assertIsNotNone is clearer. Would add as a lint rule rather than a learning.",
                created_at=_ago(hours=3),
            ),
            AgentMessage(
                task_id="task-rate-tiers",
                direction="from_agent",
                sender="Hazel.virtual",
                message_type="insight",
                content="For the api-gateway repo: tests run via `pytest -q tests/` but the README says `make test` which triggers a full Docker build. Worth noting before someone tries the Makefile on a laptop.",
                created_at=_ago(hours=4),
            ),
            AgentMessage(
                task_id="task-auth-pkce",
                direction="from_agent",
                sender="Mochi.flow",
                message_type="insight",
                content="The auth-service CI stage is flaky on the integration-test job around ~1 in 6 runs. Pattern looks like DB container race on startup.",
                created_at=_ago(hours=5),
            ),
        ]
        ritual_count = AgentMessage.query.filter(
            AgentMessage.message_type.in_(["feedback", "insight"])
        ).count()
        if ritual_count == 0:
            for m in ritual_messages:
                db.session.add(m)
                added += 1

        # --- Insights in the Pack Insights playbook ---
        demo_insights = [
            {
                "text": "Use IntelliJ to run tests in auth-service — the CLI runner (`make test`) spins up a full Docker stack and takes ~8 min. IntelliJ's pytest config hits the in-memory DB directly and runs in seconds.",
                "repo": "org/auth-service",
                "tags": ["testing", "workflow"],
            },
            {
                "text": "The api-gateway repo is mid-migration from Gorilla Mux to chi router. When touching any route handler, check whether the feature flag `USE_CHI` is on — handlers are registered in both frameworks until cutover.",
                "repo": "org/api-gateway",
                "tags": ["migration", "gotcha"],
            },
            {
                "text": "Slack channel #auth-oncall is the right place to drop questions if you're touching session or token code. The DMs to Sarah get lost; she batches the channel at end of day.",
                "tags": ["team", "communication"],
            },
        ]
        existing_insight_count = Insight.query.filter_by(status="active").count()
        if existing_insight_count < 3:
            for ins in demo_insights:
                existing = Insight.query.filter_by(text=ins["text"]).first()
                if not existing:
                    db.session.add(Insight(
                        text=ins["text"],
                        repo_scope=ins.get("repo"),
                        tags=ins.get("tags", []),
                        status="active",
                        created_at=_ago(days=7),
                    ))
                    added += 1

        # --- Pre-baked home overview so the page renders immediately ---
        # Lives as a file cache at data/overview.json (the overview
        # system graduated out of SkillResult in the Memo refactor).
        # Skip if the file already exists so demo regenerations don't
        # clobber a real overview the user just generated.
        import os as _os
        from planet_maiko.brain.overview import _overview_cache_path
        cache_path = _overview_cache_path()
        if not _os.path.exists(cache_path):
            overview_json = {
                "greeting": "Morning, Brigitte. Wednesday. 🐾",
                "summary": "The PKCE work landed clean — Mochi's got it up for review with two tiny comments worth a look. Hazel's stuck on the multi-key org question again (we should just pick one), and Biscuit's testing the token refresh race. Nothing on fire. Quiet-ish morning, honestly.",
                "focus": [
                    {"task_id": "task-auth-pkce", "why": "Mochi's review is ready — the retry-path comment is the one to actually read."},
                    {"task_id": "task-rate-tiers", "why": "Hazel's blocked on the multi-key org question. A 30-second answer unblocks her."},
                    {"task_id": "task-auth-tests", "why": "The concurrent-refresh test is the last thing between us and the auth branch merging."},
                ],
                "needs": [
                    {"pupdate_id": "pup-demo-review-ready", "summary": "PKCE review ready — two small comments on the retry path."},
                    {"pupdate_id": "pup-demo-plan-ready", "summary": "Hazel's rate-limit-tier plan wants a nod before she writes code."},
                    {"pupdate_id": "pup-demo-stuck", "summary": "Hazel stuck on multi-key orgs."},
                ],
                "alive": "Pack's working — Mochi finished, Biscuit's in tests, Hazel's waiting on you. Pollers green, backup from 4 hours ago.",
                "custom_section": "",
                "closing": "",
            }
            _os.makedirs(_os.path.dirname(cache_path), exist_ok=True)
            blob = {
                "generated_at": _ago(minutes=8).isoformat(),
                "overview": overview_json,
            }
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(blob, f, ensure_ascii=False)
            added += 1

        if added:
            db.session.commit()
            logger.info(f"Screenshot demo: added {added} rows")
        else:
            logger.info("Screenshot demo data already present")
