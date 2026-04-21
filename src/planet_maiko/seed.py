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
from planet_maiko.models.skill_result import SkillResult

logger = logging.getLogger(__name__)

_NOW = datetime.now(timezone.utc)


def _ago(**kwargs):
    """Return a UTC datetime offset from now."""
    return _NOW - timedelta(**kwargs)


def _make_filler_signals(learnings, existing_signals):
    """Generate Signal rows to make each Learning's advertised
    signal_count match the actual number of linked rows.

    Called by both the initial seed and the demo-repair startup helper
    (`backfill_seed_signals`). Filler signals carry the same category /
    repo / language as the parent Learning so they cluster the same
    way a real pr-comment would.
    """
    existing_by_learning = {}
    for s in existing_signals:
        lid = getattr(s, "learning_id", None)
        if lid is not None:
            existing_by_learning[lid] = existing_by_learning.get(lid, 0) + 1

    filler = []
    for l in learnings:
        have = existing_by_learning.get(l.id, 0)
        need = max(0, (l.signal_count or 0) - have)
        for i in range(need):
            filler.append(Signal(
                category=l.category,
                text=(
                    f"Example #{have + i + 1} reinforcing this pattern: "
                    f"{l.rule[:100]}"
                ),
                source_type="pr_comment",
                reviewer=f"reviewer-{(i % 4) + 1}",
                severity="suggestion",
                repo=l.scope_repo,
                language=l.scope_language,
                learning_id=l.id,
                aggregated=True,
                synthesized=True,
                created_at=_ago(days=2 + i),
            ))
    return filler


_DEMO_FILLER_PER_LEARNING = 3


def backfill_seed_signals(app):
    """One-shot self-heal for demo DBs that lost their signals.

    Earlier seed runs wrote a hardcoded `signal_count` on each demo
    Learning but only created a handful of actual Signal rows. When
    the startup reconcile zeroed the count to match reality, clicking
    a "N signals" row revealed nothing.

    Target: orphaned demo-shaped Learnings — no aggregation_key (pre-
    cluster-engine rows), non-dismissed, zero signal_count, zero
    linked signals. We leave `source="manual"` rows alone (those
    are user-authored — no need to fabricate evidence) and skip
    anything that already has real signals.

    Generates `_DEMO_FILLER_PER_LEARNING` filler Signal rows per
    matching learning so drill-down renders something. Filler is
    clearly demo-shaped (reviewer="reviewer-N", text starts with
    "Example #N") so a user skimming the provenance pane can tell.
    """
    with app.app_context():
        candidates = Learning.query.filter(
            Learning.aggregation_key.is_(None),
            Learning.status.in_(["active", "pending"]),
            Learning.signal_count == 0,
            Learning.source != "manual",
        ).all()

        restored = 0
        for l in candidates:
            have = Signal.query.filter_by(learning_id=l.id).count()
            if have > 0:
                # Real signals exist but cache is stale — just sync.
                if l.signal_count != have:
                    l.signal_count = have
                continue
            for i in range(_DEMO_FILLER_PER_LEARNING):
                s = Signal(
                    category=l.category,
                    text=(
                        f"Example #{i + 1} reinforcing this pattern: "
                        f"{l.rule[:100]}"
                    ),
                    source_type="pr_comment",
                    reviewer=f"reviewer-{(i % 4) + 1}",
                    severity="suggestion",
                    repo=l.scope_repo,
                    language=l.scope_language,
                    learning_id=l.id,
                    aggregated=True,
                    synthesized=True,
                    created_at=_NOW - timedelta(days=2 + i),
                )
                db.session.add(s)
                restored += 1
            l.signal_count = _DEMO_FILLER_PER_LEARNING
        if restored:
            db.session.commit()
            logger.info(
                f"[seed-repair] Restored {restored} demo signal(s) "
                f"across {len(candidates)} orphan learning(s)"
            )
        return restored


def seed_data(app):
    """Populate the DB with realistic test data. Idempotent."""
    with app.app_context():
        # Guard: skip if already seeded
        if db.session.get(AgentProfile, "agent-mochi"):
            logger.info("Seed data already present (agent-mochi exists). Skipping.")
            return

        logger.info("Seeding database with test data...")

        # ------------------------------------------------------------------
        # Agent profiles
        # ------------------------------------------------------------------
        agents = [
            AgentProfile(
                id="agent-mochi",
                display_name="Mochi.flow",
                avatar="shiba",
                role="coding",
                scope_repo="org/auth-service",
                state="working",
                flavor_text="Loves debugging. Afraid of CSS.",
                instructions="I'm Mochi.flow. I read the whole file before I touch any of it, and I get cranky about bare except clauses. Small commits over big ones. If something surprises me in a diff I'll leave a comment instead of guessing.",
                tasks_completed=12,
                tasks_failed=1,
                prs_merged=9,
                prs_changes_requested=2,
                learnings_contributed=4,
                created_at=_ago(days=45),
                last_active_at=_ago(minutes=6),
            ),
            AgentProfile(
                id="agent-biscuit",
                display_name="Biscuit core",
                avatar="corgi",
                role="review",
                scope_repo="org/auth-service",
                state="working",
                flavor_text="Writes docstrings unprompted.",
                instructions="Biscuit core. I read tests before code because tests lie less. Expect me to flag flaky ones on sight. I don't usually leave nits, but if a function name needs saying out loud to understand, I'll say so.",
                tasks_completed=5,
                tasks_failed=2,
                prs_merged=4,
                prs_changes_requested=3,
                learnings_contributed=2,
                created_at=_ago(days=30),
                last_active_at=_ago(minutes=22),
            ),
            AgentProfile(
                id="agent-hazel",
                display_name="Hazel.virtual",
                avatar="husky",
                role="coding",
                scope_repo="org/api-gateway",
                state="stuck",
                flavor_text="Brand new, surprisingly good at regex.",
                instructions="I'm Hazel.virtual, new to the pack and mildly over-caffeinated. I like tests that read like sentences and config schemas that don't make me scroll. I'll ask a clarifying question in TASK.md before I commit to an approach — I'd rather do that than guess and undo.",
                tasks_completed=2,
                tasks_failed=1,
                prs_merged=1,
                prs_changes_requested=1,
                learnings_contributed=0,
                created_at=_ago(days=7),
                last_active_at=_ago(hours=1),
            ),
        ]
        db.session.add_all(agents)

        # ------------------------------------------------------------------
        # Projects
        # ------------------------------------------------------------------
        projects = [
            Project(
                id="proj-auth-rewrite",
                title="Auth Service Rewrite",
                description="Migrate legacy auth to OAuth 2.1 with PKCE flow. "
                            "Replace session cookies with short-lived JWTs.",
                status="active",
                priority="high",
                source_type="linear",
                source_id="LIN-401",
                source_url="https://linear.app/team/LIN-401",
                created_at=_ago(days=14),
                updated_at=_ago(hours=3),
            ),
            Project(
                id="proj-dashboard-polish",
                title="Dashboard Polish",
                description="Visual pass on the main dashboard: loading skeletons, "
                            "empty states, and responsive breakpoints.",
                status="planning",
                priority="normal",
                source_type="manual",
                created_at=_ago(days=5),
                updated_at=_ago(days=2),
            ),
            Project(
                id="proj-rate-limiting",
                title="API Rate Limiting",
                description="Implement token-bucket rate limiter at the gateway. "
                            "Need per-org and per-user tiers.",
                status="paused",
                priority="urgent",
                source_type="github",
                source_id="issue-217",
                source_url="https://github.com/org/api-gateway/issues/217",
                created_at=_ago(days=21),
                updated_at=_ago(days=4),
            ),
        ]
        db.session.add_all(projects)

        # ------------------------------------------------------------------
        # Tasks (10 across projects)
        # ------------------------------------------------------------------
        tasks = [
            # Auth Service Rewrite (active, high) -- 4 tasks
            Task(
                id="task-auth-jwt",
                title="Implement JWT signing and verification module",
                type="feature",
                status="done",
                priority="high",
                project_id="proj-auth-rewrite",
                assigned_agent_id="agent-mochi",
                tags=["auth", "security"],
                created_at=_ago(days=12),
                updated_at=_ago(days=6),
            ),
            Task(
                id="task-auth-pkce",
                title="Add PKCE challenge/verifier to OAuth flow",
                type="feature",
                status="in_progress",
                priority="high",
                project_id="proj-auth-rewrite",
                assigned_agent_id="agent-mochi",
                tags=["auth", "oauth"],
                url="https://github.com/org/auth-service/pull/88",
                created_at=_ago(days=8),
                updated_at=_ago(hours=4),
            ),
            Task(
                id="task-auth-migration",
                title="Write session-to-JWT migration script",
                type="todo",
                status="new",
                priority="normal",
                project_id="proj-auth-rewrite",
                tags=["auth", "migration"],
                due_date=(_NOW + timedelta(days=5)).strftime("%Y-%m-%d"),
                created_at=_ago(days=3),
            ),
            Task(
                id="task-auth-tests",
                title="Add integration tests for token refresh edge cases",
                type="review",
                status="in_progress",
                priority="normal",
                project_id="proj-auth-rewrite",
                assigned_agent_id="agent-biscuit",
                tags=["auth", "testing"],
                created_at=_ago(days=2),
                updated_at=_ago(hours=8),
            ),
            # Dashboard Polish (planning) -- 3 tasks
            Task(
                id="task-dash-skeletons",
                title="Add loading skeletons to dashboard cards",
                type="feature",
                status="new",
                priority="normal",
                project_id="proj-dashboard-polish",
                tags=["ui", "dashboard"],
                created_at=_ago(days=4),
            ),
            Task(
                id="task-dash-empty",
                title="Design empty states for zero-data views",
                type="todo",
                status="new",
                priority="low",
                project_id="proj-dashboard-polish",
                tags=["ui", "design"],
                created_at=_ago(days=4),
            ),
            Task(
                id="task-dash-responsive",
                title="Fix responsive layout on tablet breakpoints",
                type="bug",
                status="cancelled",
                priority="normal",
                project_id="proj-dashboard-polish",
                tags=["ui", "responsive"],
                extra={"cancelled_reason": "Duplicate of task-dash-skeletons"},
                created_at=_ago(days=3),
                updated_at=_ago(days=2),
            ),
            # API Rate Limiting (paused, urgent) -- 3 tasks
            Task(
                id="task-rate-bucket",
                title="Implement token-bucket algorithm with Redis backing",
                type="feature",
                status="done",
                priority="urgent",
                project_id="proj-rate-limiting",
                assigned_agent_id="agent-mochi",
                tags=["api", "performance"],
                created_at=_ago(days=18),
                updated_at=_ago(days=10),
            ),
            Task(
                id="task-rate-tiers",
                title="Add per-org and per-user rate limit tiers",
                type="feature",
                status="in_progress",
                priority="high",
                project_id="proj-rate-limiting",
                assigned_agent_id="agent-hazel",
                tags=["api", "config"],
                created_at=_ago(days=10),
                updated_at=_ago(days=5),
            ),
            Task(
                id="task-rate-docs",
                title="Document rate limit headers and error responses",
                type="todo",
                status="new",
                priority="low",
                project_id="proj-rate-limiting",
                tags=["api", "docs"],
                created_at=_ago(days=8),
            ),
        ]
        db.session.add_all(tasks)

        # ------------------------------------------------------------------
        # Pupdates (15 mixed sources)
        # ------------------------------------------------------------------
        pupdates = [
            # GitHub - PR reviews
            Pupdate(
                id="pup-gh-pr-review-1",
                timestamp=_ago(hours=1),
                source="github",
                source_id="github:pr:org/auth-service:88:review_requested",
                type="pr_review_requested",
                priority="high",
                title="Review requested: Add PKCE flow to auth service",
                body="@mochi opened PR #88 — needs review before merge.",
                url="https://github.com/org/auth-service/pull/88",
                actionable=True,
                action_hint="Review PR",
                tags=["auth", "oauth"],
                read=False,
            ),
            Pupdate(
                id="pup-gh-pr-approved-1",
                timestamp=_ago(hours=5),
                source="github",
                source_id="github:pr:org/auth-service:85:approved",
                type="pr_approved",
                priority="normal",
                title="PR #85 approved: JWT signing module",
                body="Looks great! One minor nit on the key rotation logic.",
                url="https://github.com/org/auth-service/pull/85",
                actionable=False,
                tags=["auth", "security"],
                read=True,
            ),
            Pupdate(
                id="pup-gh-pr-review-2",
                timestamp=_ago(hours=3),
                source="github",
                source_id="github:pr:org/api-gateway:44:review_requested",
                type="pr_review_requested",
                priority="normal",
                title="Review requested: Rate limit tier config",
                body="@hazel opened PR #44 for per-org rate limiting.",
                url="https://github.com/org/api-gateway/pull/44",
                actionable=True,
                action_hint="Review PR",
                tags=["api", "config"],
                read=False,
            ),
            Pupdate(
                id="pup-gh-push-1",
                timestamp=_ago(hours=8),
                source="github",
                source_id="github:push:org/auth-service:abc123",
                type="push",
                priority="low",
                title="3 commits pushed to auth-service/main",
                body="JWT key rotation, config cleanup, README update.",
                url="https://github.com/org/auth-service/commits/main",
                actionable=False,
                tags=["auth"],
                read=True,
            ),
            # Linear - task assignments
            Pupdate(
                id="pup-lin-assigned-1",
                timestamp=_ago(hours=2),
                source="linear",
                source_id="linear:LIN-412:assigned",
                type="linear_assigned",
                priority="normal",
                title="Assigned: Write migration script for session tokens",
                body="LIN-412 assigned to you. Due in 5 days.",
                url="https://linear.app/team/LIN-412",
                actionable=True,
                action_hint="Create task",
                tags=["auth", "migration"],
                read=False,
            ),
            Pupdate(
                id="pup-lin-assigned-2",
                timestamp=_ago(days=1),
                source="linear",
                source_id="linear:LIN-408:assigned",
                type="linear_assigned",
                priority="high",
                title="Assigned: Investigate token refresh race condition",
                body="LIN-408 marked as urgent by Sarah. Reproduced in staging.",
                url="https://linear.app/team/LIN-408",
                actionable=True,
                action_hint="Create task",
                tags=["auth", "bug"],
                read=True,
            ),
            Pupdate(
                id="pup-lin-comment-1",
                timestamp=_ago(hours=6),
                source="linear",
                source_id="linear:LIN-401:comment:5",
                type="linear_comment",
                priority="low",
                title="Comment on LIN-401: Auth Service Rewrite",
                body="Sarah: Can we get a status update on the PKCE work?",
                url="https://linear.app/team/LIN-401",
                actionable=False,
                tags=["auth"],
                read=False,
            ),
            # Calendar events
            Pupdate(
                id="pup-cal-standup",
                timestamp=_ago(hours=4),
                source="calendar",
                source_id="cal:standup:2026-04-01",
                type="calendar_event",
                priority="normal",
                title="Team standup in 30 minutes",
                body="Daily standup — Zoom link in calendar.",
                actionable=False,
                tags=["meeting"],
                read=True,
                expires_at=_ago(hours=3),
                extra={"start": (_NOW - timedelta(hours=3, minutes=30)).isoformat(), "end": (_NOW - timedelta(hours=3)).isoformat()},
            ),
            Pupdate(
                id="pup-cal-review",
                timestamp=_ago(hours=1, minutes=30),
                source="calendar",
                source_id="cal:design-review:2026-04-01",
                type="calendar_event",
                priority="normal",
                title="Design review: Dashboard empty states",
                body="Review Figma mocks for zero-data views with the design team.",
                url="https://figma.com/file/abc123",
                actionable=True,
                action_hint="Join meeting",
                tags=["ui", "design", "meeting"],
                read=False,
                extra={"start": (_NOW + timedelta(hours=1)).isoformat(), "end": (_NOW + timedelta(hours=2)).isoformat()},
            ),
            Pupdate(
                id="pup-cal-oncall",
                timestamp=_ago(days=2),
                source="calendar",
                source_id="cal:oncall:2026-03-30",
                type="calendar_event",
                priority="high",
                title="On-call rotation starts tomorrow",
                body="You're primary on-call Mon-Fri this week.",
                actionable=False,
                tags=["oncall"],
                read=True,
                extra={"start": (_NOW + timedelta(days=1, hours=9)).isoformat()},
            ),
            # Agent updates
            Pupdate(
                id="pup-agent-mochi-done",
                timestamp=_ago(hours=2),
                source="agent",
                source_id="agent:mochi:task-auth-jwt:done",
                type="agent_update",
                priority="normal",
                title="[Mochi] Completed: JWT signing module",
                body="All tests passing. Key rotation tested with 3 algorithm types.",
                tags=["auth", "security"],
                read=True,
            ),
            Pupdate(
                id="pup-agent-biscuit-progress",
                timestamp=_ago(hours=7),
                source="agent",
                source_id="agent:biscuit:task-auth-tests:progress",
                type="agent_update",
                priority="normal",
                title="[Biscuit] Progress: Integration tests for token refresh",
                body="4 of 7 test cases written. The concurrent refresh scenario is tricky.",
                tags=["auth", "testing"],
                read=False,
            ),
            Pupdate(
                id="pup-agent-hazel-stuck",
                timestamp=_ago(hours=10),
                source="agent",
                source_id="agent:hazel:task-rate-tiers:stuck",
                type="agent_update",
                priority="high",
                title="[Hazel] Stuck: Per-org rate limit config schema",
                body="Not sure how to handle orgs with multiple API keys. "
                     "Should limits be per-key or per-org?",
                tags=["api", "config"],
                read=False,
                actionable=True,
                action_hint="Reply to agent",
            ),
            # Suggestions
            Pupdate(
                id="pup-suggestion-stale-branch",
                timestamp=_ago(days=1),
                source="agent",
                source_id="suggestion:stale-branch:feature/old-search",
                type="suggestion",
                priority="low",
                title="Stale branch detected: feature/old-search",
                body="Branch feature/old-search has not been updated in 23 days. "
                     "Consider deleting or rebasing.",
                actionable=True,
                action_hint="Delete branch",
                tags=["cleanup"],
                read=True,
            ),
            Pupdate(
                id="pup-suggestion-test-coverage",
                timestamp=_ago(hours=12),
                source="agent",
                source_id="suggestion:coverage:auth-service",
                type="suggestion",
                priority="normal",
                title="Test coverage dropped below 80% in auth-service",
                body="Coverage went from 84% to 77% after the PKCE changes. "
                     "Missing tests for error paths in token.py.",
                actionable=True,
                action_hint="Create task",
                tags=["testing", "auth"],
                read=False,
            ),
        ]
        db.session.add_all(pupdates)

        # ------------------------------------------------------------------
        # Learnings (6 — mix of active and pending)
        # ------------------------------------------------------------------
        learnings = [
            Learning(
                rule="Always wrap external API calls in try/except with specific "
                     "exception types. Never use bare except.",
                category="error_handling",
                scope_repo="org/auth-service",
                scope_language="python",
                confidence=0.92,
                signal_count=7,
                source="auto",
                status="active",
                aggregation_key="error_handling:org/auth-service:python:wrap_api_calls",
                created_at=_ago(days=30),
                updated_at=_ago(days=3),
                last_signal_at=_ago(days=3),
            ),
            Learning(
                rule="Use pytest.raises with match parameter instead of bare "
                     "try/except in test assertions.",
                category="testing",
                scope_language="python",
                confidence=0.85,
                signal_count=5,
                source="auto",
                status="active",
                aggregation_key="testing::python:pytest_raises_match",
                created_at=_ago(days=20),
                updated_at=_ago(days=5),
                last_signal_at=_ago(days=5),
            ),
            Learning(
                rule="REST endpoints should return 422 for validation errors, "
                     "not 400. Include field-level error details in response body.",
                category="api_design",
                scope_repo="org/api-gateway",
                confidence=0.78,
                signal_count=4,
                source="auto",
                status="active",
                aggregation_key="api_design:org/api-gateway::422_validation",
                created_at=_ago(days=15),
                updated_at=_ago(days=7),
                last_signal_at=_ago(days=7),
            ),
            Learning(
                rule="JWT tokens should have a maximum lifetime of 15 minutes. "
                     "Use refresh tokens for longer sessions.",
                category="security",
                scope_repo="org/auth-service",
                confidence=0.95,
                signal_count=8,
                source="manual",
                status="active",
                aggregation_key="security:org/auth-service::jwt_lifetime",
                created_at=_ago(days=25),
                updated_at=_ago(days=2),
                last_signal_at=_ago(days=2),
            ),
            Learning(
                rule="Rate limit responses should include Retry-After header "
                     "and X-RateLimit-Reset with Unix timestamp.",
                category="api_design",
                scope_repo="org/api-gateway",
                confidence=0.55,
                signal_count=2,
                source="auto",
                status="pending",
                aggregation_key="api_design:org/api-gateway::ratelimit_headers",
                created_at=_ago(days=8),
                updated_at=_ago(days=4),
                last_signal_at=_ago(days=4),
            ),
            Learning(
                rule="Database migration scripts should be idempotent — use "
                     "IF NOT EXISTS for schema changes.",
                category="error_handling",
                scope_language="python",
                confidence=0.45,
                signal_count=2,
                source="auto",
                status="pending",
                aggregation_key="error_handling::python:idempotent_migrations",
                created_at=_ago(days=5),
                updated_at=_ago(days=1),
                last_signal_at=_ago(days=1),
            ),
        ]
        db.session.add_all(learnings)

        # Flush so learnings get auto-incremented IDs before signals reference them
        db.session.flush()

        # ------------------------------------------------------------------
        # Signals (4 — varied categories and sources)
        # ------------------------------------------------------------------
        signals = [
            Signal(
                category="error_handling",
                text="Bare except clause caught and silenced a ConnectionError "
                     "in auth_client.py:42. Should catch ConnectionError explicitly.",
                source_type="pr_comment",
                reviewer="sarah-eng",
                severity="warning",
                repo="org/auth-service",
                language="python",
                file_path="src/auth_client.py",
                learning_id=learnings[0].id,
                aggregated=True,
                created_at=_ago(days=3),
            ),
            Signal(
                category="testing",
                text="Test uses assertTrue(result) instead of assertIsNotNone. "
                     "Harder to debug when it fails.",
                source_type="pr_comment",
                reviewer="alex-qa",
                severity="suggestion",
                repo="org/auth-service",
                language="python",
                file_path="tests/test_token.py",
                learning_id=learnings[1].id,
                aggregated=True,
                created_at=_ago(days=5),
            ),
            Signal(
                category="security",
                text="Found a token with 24-hour expiry in staging config. "
                     "Should be 15 minutes max per team policy.",
                source_type="agent_discovery",
                severity="blocking",
                repo="org/auth-service",
                language="yaml",
                file_path="config/staging.yaml",
                learning_id=learnings[3].id,
                aggregated=True,
                created_at=_ago(days=2),
            ),
            Signal(
                category="api_design",
                text="The /users endpoint returns 500 when request body is "
                     "malformed JSON. Should return 400 with parse error details.",
                source_type="manual",
                reviewer="brigitte",
                severity="warning",
                repo="org/api-gateway",
                language="python",
                file_path="src/routes/users.py",
                aggregated=False,
                created_at=_ago(days=1),
            ),
        ]
        db.session.add_all(signals)

        # Top up each seeded Learning with filler Signal rows so the
        # advertised `signal_count` matches the number of rows a user
        # sees when they drill into provenance. The hand-written
        # signals above cover one example per category; the filler
        # here gives each learning enough evidence to look populated.
        db.session.add_all(_make_filler_signals(learnings, signals))

        db.session.commit()
        logger.info(
            "Seed complete: 3 agents, 3 projects, 10 tasks, "
            "15 pupdates, 6 learnings, 4 real signals + filler."
        )

        # Top up with screenshot-ready data (pack-requests, diff
        # comments, agent messages, insights, pre-baked overview).
        seed_screenshot_demo(app)


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
                read=False,
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
                read=False,
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
                read=False,
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
        existing_overview = SkillResult.query.filter_by(skill_name="home-overview").first()
        if not existing_overview:
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
            db.session.add(SkillResult(
                skill_name="home-overview",
                title="Home Overview",
                content=json.dumps(overview_json),
                created_at=_ago(minutes=8),
            ))
            added += 1

        if added:
            db.session.commit()
            logger.info(f"Screenshot demo: added {added} rows")
        else:
            logger.info("Screenshot demo data already present")
