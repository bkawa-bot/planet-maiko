"""Seed the database with realistic test data.

Idempotent: checks for existence of 'agent-mochi' before inserting.
"""

import logging
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.models.project import Project
from planet_maiko.models.task import Task
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.learning import Learning
from planet_maiko.models.signal import Signal

logger = logging.getLogger(__name__)

_NOW = datetime.now(timezone.utc)


def _ago(**kwargs):
    """Return a UTC datetime offset from now."""
    return _NOW - timedelta(**kwargs)


def seed_data(app):
    """Populate the DB with realistic test data. Idempotent."""
    with app.app_context():
        # Guard: skip if already seeded
        if AgentProfile.query.get("agent-mochi"):
            logger.info("Seed data already present (agent-mochi exists). Skipping.")
            return

        logger.info("Seeding database with test data...")

        # ------------------------------------------------------------------
        # Agent profiles
        # ------------------------------------------------------------------
        agents = [
            AgentProfile(
                id="agent-mochi",
                display_name="Mochi",
                avatar="shiba",
                breed="senior",
                flavor_text="Loves debugging. Afraid of CSS. Will refactor anything that sits still.",
                tasks_completed=12,
                tasks_failed=1,
                prs_merged=9,
                prs_changes_requested=2,
                learnings_contributed=4,
                specializations={
                    "auth-service": 0.92,
                    "api-gateway": 0.78,
                    "user-service": 0.65,
                },
                created_at=_ago(days=45),
                last_active_at=_ago(hours=2),
            ),
            AgentProfile(
                id="agent-biscuit",
                display_name="Biscuit",
                avatar="corgi",
                breed="junior",
                flavor_text="Enthusiastic about tests. Writes docstrings unprompted. Naps after lunch.",
                tasks_completed=5,
                tasks_failed=2,
                prs_merged=4,
                prs_changes_requested=3,
                learnings_contributed=2,
                specializations={
                    "dashboard-ui": 0.70,
                    "notification-service": 0.55,
                    "search-service": 0.40,
                },
                created_at=_ago(days=30),
                last_active_at=_ago(hours=6),
            ),
            AgentProfile(
                id="agent-hazel",
                display_name="Hazel",
                avatar="husky",
                breed="pup",
                flavor_text="Brand new and full of questions. Surprisingly good at regex.",
                tasks_completed=2,
                tasks_failed=1,
                prs_merged=1,
                prs_changes_requested=1,
                learnings_contributed=0,
                specializations={
                    "api-gateway": 0.30,
                    "docs": 0.25,
                },
                created_at=_ago(days=7),
                last_active_at=_ago(days=1),
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

        db.session.commit()
        logger.info(
            "Seed complete: 3 agents, 3 projects, 10 tasks, "
            "15 pupdates, 6 learnings, 4 signals."
        )
