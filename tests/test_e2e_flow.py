"""End-to-end integration tests for the full Planet Maiko agent lifecycle.

Tests the complete flow:
1. Create task from pupdate
2. Recommend agent
3. Assign agent (prepare worktree/branch, inject learnings)
4. Verify TASK.md and CLAUDE.md were written correctly
5. Simulate agent communication (report progress, check inbox)
6. Simulate mid-session feedback
7. Complete task and record outcome
8. Verify specialization scores updated
9. Verify lens (overrides, gaps, territory) updated
10. Run brain cycle and verify all phases execute
11. Test project driver auto-advancement
"""

import json
import os
import subprocess
import tempfile

import pytest

from planet_maiko.database import db as _db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.models.task import Task
from planet_maiko.models.project import Project
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.models.signal import Signal
from planet_maiko.models.learning import Learning
from planet_maiko.models.context_selection import ContextSelection
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.models.skill_result import SkillResult
from planet_maiko.models.custom_skill import CustomSkill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_temp_git_repo(tmp_path):
    """Create a bare-minimum git repo that prepare() can branch from."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return repo


def _seed_learnings(db, count=3):
    """Add a few active learnings for brief compilation."""
    items = [
        ("error_handling", "Always wrap API calls in try/except"),
        ("testing", "Write tests before implementation"),
        ("security", "Validate all user input"),
    ]
    created = []
    for i, (cat, rule) in enumerate(items[:count]):
        learning = Learning(
            rule=rule,
            category=cat,
            status="active",
            confidence=0.8,
            signal_count=5,
            source="manual",
        )
        db.session.add(learning)
        created.append(learning)
    db.session.flush()
    return created


# ---------------------------------------------------------------------------
# Test 1: Full Agent Lifecycle
# ---------------------------------------------------------------------------


class TestFullAgentLifecycle:
    """pupdate -> task -> recommend -> assign -> prepare -> communicate
    -> feedback -> complete -> learn"""

    def test_lifecycle(self, app, db, tmp_path):
        # ---- 1. Create a pupdate (simulating a GitHub PR) ----
        pupdate = Pupdate(
            id="pup-e2e-1",
            source="github",
            type="pr_review_requested",
            title="PR #42: Add input validation",
            body="Please review this PR adding validation to the API.",
            url="https://github.com/org/repo/pull/42",
            priority="high",
            actionable=True,
            action_hint="Review PR",
            tags=["api", "validation"],
            extra={"repo": "org/repo", "number": 42},
        )
        db.session.add(pupdate)

        # ---- 2. Create a task from the pupdate ----
        task = Task(
            id="task-e2e-1",
            title="Review PR #42: Add input validation",
            type="pr_review",
            priority="high",
            source_pupdate_id="pup-e2e-1",
            url="https://github.com/org/repo/pull/42",
            tags=["api"],
            extra={"repo": "org/repo"},
        )
        db.session.add(task)

        # ---- 3. Seed learnings ----
        learnings = _seed_learnings(db)
        db.session.commit()

        # ---- 4. Recommend an agent (no agents yet -> gap) ----
        from planet_maiko.agents.profiles import recommend_agent
        recs = recommend_agent(repo="org/repo")
        assert any(r.get("gap_detected") for r in recs), (
            "Expected gap_detected since no agents exist"
        )

        # ---- 5. Create an agent profile ----
        from planet_maiko.agents.profiles import create_profile
        profile = create_profile("agent-e2e-1")
        assert profile.display_name
        assert profile.avatar

        # ---- 6. Compile brief for this agent ----
        from planet_maiko.brain.learning.processor import compile_brief
        brief = compile_brief(
            repo="org/repo",
            task_id="task-e2e-1",
            agent_profile_id="agent-e2e-1",
        )
        assert "Coding Guidelines" in brief
        # At least one of our seeded rules should appear
        assert any(l.rule in brief for l in learnings)

        # ---- 7. Verify ContextSelection was created ----
        selections = ContextSelection.query.filter_by(task_id="task-e2e-1").all()
        assert len(selections) == 1
        sel = selections[0]
        assert sel.agent_profile_id == "agent-e2e-1"
        assert len(sel.learning_ids) > 0

        # ---- 8. Prepare agent work area (branch-only, no worktree for speed) ----
        repo_path = _make_temp_git_repo(tmp_path)
        from planet_maiko.agents.coding_agent import _write_task_file, _write_claude_md
        work_dir = str(tmp_path / "workdir")
        os.makedirs(work_dir)

        _write_task_file(work_dir, "task-e2e-1", "Review PR #42", "Review and approve the PR")
        task_md = os.path.join(work_dir, "TASK.md")
        assert os.path.exists(task_md)
        with open(task_md, encoding="utf-8") as f:
            content = f.read()
        assert "task-e2e-1" in content
        assert "Review and approve the PR" in content

        _write_claude_md(work_dir, "task-e2e-1", "Review PR #42")
        claude_md = os.path.join(work_dir, "CLAUDE.md")
        assert os.path.exists(claude_md)
        with open(claude_md, encoding="utf-8") as f:
            content = f.read()
        assert "maiko report" in content
        assert "maiko feedback" in content
        assert "task-e2e-1" in content

        # ---- 9. Simulate agent communication via inbox ----
        msg_to = AgentMessage(
            task_id="task-e2e-1",
            direction="to_agent",
            sender="user",
            content="Please also check the edge cases",
            message_type="message",
        )
        db.session.add(msg_to)

        msg_from = AgentMessage(
            task_id="task-e2e-1",
            direction="from_agent",
            sender="agent",
            content="Reviewing edge cases now!",
            message_type="message",
        )
        db.session.add(msg_from)
        db.session.commit()

        all_msgs = AgentMessage.query.filter_by(task_id="task-e2e-1").all()
        assert len(all_msgs) == 2
        assert {m.direction for m in all_msgs} == {"to_agent", "from_agent"}

        # ---- 10. Simulate mid-session feedback ----
        from planet_maiko.agents.profiles import record_session_feedback
        count = record_session_feedback("task-e2e-1", "testing", "warning")
        assert count == 1

        # ---- 11. Record task outcome ----
        from planet_maiko.agents.profiles import record_task_outcome
        recorded = record_task_outcome("task-e2e-1", "success")
        assert recorded == 1

        # ---- 12. Verify profile stats updated ----
        profile = db.session.get(AgentProfile, "agent-e2e-1")
        assert profile.tasks_completed >= 1

        # ---- 13. Verify lens territory updated ----
        lens = profile.lens or {}
        territory = lens.get("territory", {})
        assert "org/repo" in territory
        assert territory["org/repo"]["_total"] >= 1

        # ---- 14. Verify ContextSelection outcome recorded ----
        sel = ContextSelection.query.filter_by(task_id="task-e2e-1").first()
        assert sel.outcome == "success"
        assert sel.outcome_recorded_at is not None


# ---------------------------------------------------------------------------
# Test 2: Brain Cycle Phases
# ---------------------------------------------------------------------------


class TestBrainCycle:
    """Verify the brain cycle runs all phases without errors."""

    def test_brain_cycle_runs_all_phases(self, app, db):
        # Seed minimal data so each phase has something to chew on
        pup = Pupdate(
            id="pup-cycle-1", source="github", type="pr_review_requested",
            title="Cycle test pupdate", priority="normal",
        )
        db.session.add(pup)
        db.session.commit()

        from planet_maiko.brain.cycle import run
        results = run(app)

        # The cycle should return a dict with keys for each phase
        assert isinstance(results, dict)

        expected_phases = [
            "agents", "awareness", "correlator", "pupdates", "learning",
            "heartbeats", "projects",
        ]
        for phase in expected_phases:
            assert phase in results, f"Missing phase '{phase}' in cycle results"

    def test_brain_cycle_processes_pupdates(self, app, db):
        pup = Pupdate(
            id="pup-cycle-proc", source="github", type="pr_ci_passed",
            title="CI passed for PR #10", priority="low",
        )
        db.session.add(pup)
        db.session.commit()

        from planet_maiko.brain.cycle import run
        results = run(app)

        # The pupdate should now be brain_processed
        refreshed = db.session.get(Pupdate, "pup-cycle-proc")
        assert refreshed.brain_processed is True

    def test_brain_cycle_processes_signals(self, app, db, monkeypatch):
        # Clustering is LLM-driven; stub the call so offline tests still
        # exercise the cycle's aggregation path. One cluster per signal
        # → one new Learning per signal (simplest valid clustering).
        from planet_maiko.brain.learning import clustering as _clustering
        def fake_attach(category, existing, signals):
            return [
                {"existing_id": None, "canonical": s.text[:80], "member_ids": [s.id]}
                for s in signals
            ], True
        monkeypatch.setattr(_clustering, "_call_attach_llm", fake_attach)

        # Manual signals carry a real category, mirror that here so
        # clustering picks them up (the cluster path skips any signal
        # with synthesized=False, waiting for LLM synthesis to settle
        # the category first).
        sig = Signal(
            category="style", text="Keep functions under 30 lines",
            source_type="manual", synthesized=True,
        )
        db.session.add(sig)
        db.session.commit()

        from planet_maiko.brain.cycle import run
        results = run(app)

        assert results["learning"]["processed"] >= 1

        refreshed = Signal.query.first()
        assert refreshed.aggregated is True


# ---------------------------------------------------------------------------
# Test 3: Project Driver Auto-Advancement
# ---------------------------------------------------------------------------


class TestProjectDriver:

    def test_advances_when_phase_done(self, app, db):
        """When phase 0 is already marked done, driver advances to phase 1."""
        project = Project(
            id="proj-e2e",
            title="Test Project",
            status="active",
            phases=[
                {"number": 0, "title": "Phase 1", "status": "done", "repo": "org/repo"},
                {"number": 1, "title": "Phase 2", "status": "pending", "repo": "org/repo"},
            ],
            current_phase=0,
        )
        db.session.add(project)
        db.session.commit()

        from planet_maiko.brain.projects.driver import drive_projects

        result = drive_projects()
        assert result["advanced"] >= 1

        project = db.session.get(Project, "proj-e2e")
        assert project.current_phase == 1
        assert project.phases[1]["status"] == "active"

    def test_marks_phase_done_when_tasks_complete(self, app, db):
        """When all tasks for a phase are done, driver marks the phase done.

        The driver marks phase done in one cycle, then advances on the next.
        """
        project = Project(
            id="proj-mark",
            title="Mark Phase Project",
            status="active",
            phases=[
                {"number": 0, "title": "Phase 1", "status": "active", "repo": "org/repo"},
                {"number": 1, "title": "Phase 2", "status": "pending", "repo": "org/repo"},
            ],
            current_phase=0,
        )
        db.session.add(project)

        task = Task(
            id="task-phase-mark-0",
            title="Phase 1 work",
            project_id="proj-mark",
            status="done",
            extra={"phase_number": 0},
        )
        db.session.add(task)
        db.session.commit()

        from planet_maiko.brain.projects.driver import drive_projects

        # First call: marks phase 0 as done (but does not advance yet)
        drive_projects()
        # The phase change may not be committed by the driver (only commits
        # when advanced or completed), so commit here for the second pass
        db.session.commit()

        project = db.session.get(Project, "proj-mark")
        assert project.phases[0]["status"] == "done"

        # Second call: sees phase done, advances to phase 1
        result2 = drive_projects()
        assert result2["advanced"] >= 1

        project = db.session.get(Project, "proj-mark")
        assert project.current_phase == 1

    def test_completes_project_after_last_phase(self, app, db):
        project = Project(
            id="proj-fin",
            title="Finishing Project",
            status="active",
            phases=[
                {"number": 0, "title": "Only Phase", "status": "done", "repo": "org/repo"},
            ],
            current_phase=0,
        )
        db.session.add(project)
        db.session.commit()

        from planet_maiko.brain.projects.driver import drive_projects
        result = drive_projects()
        assert result["completed"] >= 1

        project = db.session.get(Project, "proj-fin")
        assert project.status == "done"

    def test_creates_phase_task_on_advance(self, app, db):
        project = Project(
            id="proj-task-create",
            title="Task Create Project",
            status="active",
            phases=[
                {"number": 0, "title": "P1", "status": "done", "repo": "org/repo"},
                {"number": 1, "title": "P2", "status": "pending", "repo": "org/repo"},
            ],
            current_phase=0,
        )
        db.session.add(project)
        db.session.commit()

        from planet_maiko.brain.projects.driver import drive_projects
        drive_projects()

        # Should have created a task for the new phase
        phase_task = db.session.get(Task, "task-proj-task-create-phase-1")
        assert phase_task is not None
        assert "P2" in phase_task.title

    def test_creates_notification_on_advance(self, app, db):
        project = Project(
            id="proj-notify",
            title="Notify Project",
            status="active",
            phases=[
                {"number": 0, "title": "P1", "status": "done"},
                {"number": 1, "title": "P2", "status": "pending"},
            ],
            current_phase=0,
        )
        db.session.add(project)
        db.session.commit()

        from planet_maiko.brain.projects.driver import drive_projects
        drive_projects()

        # Should have created a notification pupdate
        notify = Pupdate.query.filter_by(type="project_phase_advanced").first()
        assert notify is not None
        assert "Notify Project" in notify.title


# ---------------------------------------------------------------------------
# Test 4: Learning Pipeline
# ---------------------------------------------------------------------------


class TestLearningPipeline:

    def test_signals_aggregate_into_learning(self, app, db):
        for i in range(5):
            sig = Signal(
                category="style",
                text="Use consistent naming conventions",
                source_type="pr_comment",
                repo="org/repo",
                language="python",
            )
            db.session.add(sig)
        db.session.commit()

        from planet_maiko.brain.learning.processor import process_signals
        counts = process_signals()

        assert counts["processed"] == 5
        assert counts["new_learnings"] == 1
        assert counts["updated_learnings"] == 4

        learning = Learning.query.filter_by(category="style").first()
        assert learning is not None
        assert learning.signal_count == 5

    def test_never_auto_graduates(self, app, db):
        """Auto-graduation was removed. Learnings always stay "pending"
        until the user explicitly approves them in the Knowledge UI —
        regardless of signal_count or category."""
        for category in ("style", "security", "error_handling"):
            for i in range(5):
                db.session.add(Signal(
                    category=category,
                    text=f"Rule for {category}",
                    source_type="pr_comment",
                ))
        db.session.commit()

        from planet_maiko.brain.learning.processor import process_signals
        counts = process_signals()

        assert counts["graduated"] == 0
        for l in Learning.query.all():
            assert l.status == "pending"
            assert l.signal_count >= 5

    def test_idempotent_processing(self, app, db):
        """Already-aggregated signals should not be processed again."""
        sig = Signal(
            category="testing",
            text="Use parameterized tests",
            source_type="manual",
            aggregated=True,
        )
        db.session.add(sig)
        db.session.commit()

        from planet_maiko.brain.learning.processor import process_signals
        counts = process_signals()
        assert counts["processed"] == 0


# ---------------------------------------------------------------------------
# Test 5: Conflict Detection (UnionFind)
# ---------------------------------------------------------------------------


class TestConflictDetection:

    def test_union_find_basic_clustering(self, app, db):
        from planet_maiko.brain.awareness.conflicts import UnionFind

        uf = UnionFind()
        uf.union("agent-1", "agent-2")
        uf.union("agent-2", "agent-3")

        # Transitive: 1, 2, 3 in the same cluster
        assert uf.find("agent-1") == uf.find("agent-3")
        # agent-4 is isolated
        assert uf.find("agent-1") != uf.find("agent-4")

    def test_union_find_disjoint_clusters(self, app, db):
        from planet_maiko.brain.awareness.conflicts import UnionFind

        uf = UnionFind()
        uf.union("a", "b")
        uf.union("c", "d")

        assert uf.find("a") == uf.find("b")
        assert uf.find("c") == uf.find("d")
        assert uf.find("a") != uf.find("c")

    def test_union_find_single_element_is_own_root(self, app, db):
        from planet_maiko.brain.awareness.conflicts import UnionFind

        uf = UnionFind()
        assert uf.find("solo") == "solo"

    def test_detect_conflicts_returns_empty_for_single_agent(self, app, db):
        from planet_maiko.brain.awareness.conflicts import detect_conflicts
        result = detect_conflicts([{"task_id": "t1", "worktree_path": "/nonexistent"}])
        assert result == []


# ---------------------------------------------------------------------------
# Test 6: Focus Mode
# ---------------------------------------------------------------------------


class TestFocusMode:

    def test_set_and_get_state(self, app, db):
        from planet_maiko.brain.focus.manager import set_state, get_state

        set_state("deep_focus", duration_minutes=60, trigger="explicit")
        state = get_state()
        assert state["current_state"] == "deep_focus"
        assert state["trigger"] == "explicit"

        # Reset to available for other tests
        set_state("available")

    def test_gate_matrix_filters_pupdates(self, app, db):
        from planet_maiko.brain.focus.manager import set_state, should_surface

        set_state("deep_focus")

        low_pup = Pupdate(
            id="pup-low", source="test", type="info",
            title="Low priority", priority="low",
        )
        high_pup = Pupdate(
            id="pup-high", source="test", type="info",
            title="High priority", priority="urgent",
        )
        critical_pup = Pupdate(
            id="pup-crit", source="test", type="deploy_rollback",
            title="Rollback", priority="normal",
        )

        assert should_surface(low_pup) is False
        assert should_surface(high_pup) is True  # urgent passes deep_focus
        assert should_surface(critical_pup) is True  # critical type always passes

        set_state("available")

    def test_calendar_auto_focus(self, app, db):
        from planet_maiko.brain.focus.manager import (
            set_state, get_state, check_calendar_focus,
        )
        from datetime import datetime, timezone, timedelta

        set_state("available")

        # Create a calendar pupdate for a meeting starting in 2 minutes
        soon = datetime.now(timezone.utc) + timedelta(minutes=2)
        cal_pup = Pupdate(
            id="pup-cal-1", source="calendar", type="meeting",
            title="Team standup", priority="normal",
            extra={"start": soon.isoformat()},
        )

        changed = check_calendar_focus([cal_pup])
        assert changed is True
        assert get_state()["current_state"] == "soft_focus"

        # Cleanup
        set_state("available")


# ---------------------------------------------------------------------------
# Test 7: Pack Insights Flow
# ---------------------------------------------------------------------------


class TestPackInsights:

    def test_full_flow(self, app, db):
        """start -> gather -> review -> synthesize -> finalize"""
        from planet_maiko.brain.learning.pack_insights import (
            start_gathering, collect_from_agents, add_manual_learning,
            synthesize, finalize, get_state, reset,
        )

        # Start
        state = start_gathering()
        assert state["status"] == "gathering"

        # Simulate an agent reporting learnings
        pup = Pupdate(
            id="pup-agent-learn-1",
            source="agent",
            type="agent_learnings",
            title="Agent learnings",
            body="- Always use explicit return types\n- Prefer composition over inheritance",
            tags=["agent-e2e-pack"],
        )
        db.session.add(pup)
        db.session.commit()

        # Collect
        state = collect_from_agents()
        assert state["status"] == "reviewing"
        assert len(state["collected"]) >= 2

        # Add a manual learning during review
        add_manual_learning("Never use eval() on user input", category="security")

        # Synthesize
        result = synthesize()
        assert "unique_learnings" in result
        assert len(result["unique_learnings"]) >= 1

        # Finalize
        stats = finalize()
        assert stats["kept"] >= 1

        # Verify signals were created
        signals = Signal.query.filter_by(source_type="agent_discovery").all()
        assert len(signals) >= 1

        reset()
        assert get_state()["status"] == "idle"


# ---------------------------------------------------------------------------
# Test 8: Feedback to Lens Overrides
# ---------------------------------------------------------------------------


class TestFeedbackToLens:

    def test_failed_task_with_feedback_adds_overrides(self, app, db):
        """When a task fails and session feedback contradicts injected learnings,
        those learnings are added to the agent's lens overrides."""
        profile = AgentProfile(
            id="agent-lens-1", display_name="Lens Bot", avatar="shiba",
        )
        db.session.add(profile)

        learning = Learning(
            rule="Always use Optional for nullable returns",
            category="null_safety",
            status="active",
            confidence=0.5,
            signal_count=5,
        )
        db.session.add(learning)
        db.session.flush()

        sel = ContextSelection(
            task_id="task-lens-1",
            agent_profile_id="agent-lens-1",
            repo="org/repo",
            learning_ids=[learning.id],
            outcome=None,
        )
        db.session.add(sel)

        # Create a session feedback signal that contradicts the injected learning
        feedback = Signal(
            category="null_safety",
            text="Optional is too verbose here, use direct null check",
            source_type="session_feedback",
            severity="warning",
        )
        db.session.add(feedback)
        db.session.commit()

        from planet_maiko.agents.profiles import record_task_outcome
        record_task_outcome("task-lens-1", "changes_requested")

        profile = db.session.get(AgentProfile, "agent-lens-1")
        lens = profile.lens or {}
        overrides = lens.get("overrides", [])
        assert learning.id in overrides

    def test_failed_task_adds_gap_for_uncovered_category(self, app, db):
        """When a task fails and feedback references a category NOT in the
        injected learnings, that category is added as a gap."""
        profile = AgentProfile(
            id="agent-gap-1", display_name="Gap Bot", avatar="corgi",
        )
        db.session.add(profile)

        # Injected learning is in "testing" category
        learning = Learning(
            rule="Always mock external deps", category="testing",
            status="active", confidence=0.5, signal_count=5,
        )
        db.session.add(learning)
        db.session.flush()

        sel = ContextSelection(
            task_id="task-gap-1",
            agent_profile_id="agent-gap-1",
            repo="org/repo",
            learning_ids=[learning.id],
            outcome=None,
        )
        db.session.add(sel)

        # But feedback is about "performance" (a gap -- no learning for it)
        feedback = Signal(
            category="performance",
            text="This query is way too slow, add an index",
            source_type="session_feedback",
            severity="blocking",
        )
        db.session.add(feedback)
        db.session.commit()

        from planet_maiko.agents.profiles import record_task_outcome
        record_task_outcome("task-gap-1", "failed")

        profile = db.session.get(AgentProfile, "agent-gap-1")
        lens = profile.lens or {}
        gaps = lens.get("gaps", [])
        gap_categories = {g["category"] for g in gaps}
        assert "performance" in gap_categories

    def test_success_updates_territory_without_overrides(self, app, db):
        """Successful tasks update territory but do not add overrides."""
        profile = AgentProfile(
            id="agent-terr-1", display_name="Terr Bot", avatar="husky",
        )
        db.session.add(profile)

        learning = Learning(
            rule="Use pytest fixtures", category="testing",
            status="active", confidence=0.5, signal_count=5,
        )
        db.session.add(learning)
        db.session.flush()

        sel = ContextSelection(
            task_id="task-terr-1",
            agent_profile_id="agent-terr-1",
            repo="my-repo",
            learning_ids=[learning.id],
            outcome=None,
        )
        db.session.add(sel)
        db.session.commit()

        from planet_maiko.agents.profiles import record_task_outcome
        record_task_outcome("task-terr-1", "success")

        profile = db.session.get(AgentProfile, "agent-terr-1")
        lens = profile.lens or {}
        assert lens.get("overrides", []) == []
        territory = lens.get("territory", {})
        assert "my-repo" in territory


# ---------------------------------------------------------------------------
# Test 9: API Smoke Tests
# ---------------------------------------------------------------------------


class TestAPISmokeTests:

    def test_assign_agent_rejects_missing_fields(self, client, app, db):
        """POST /api/agents/assign with missing fields returns error status."""
        resp = client.post("/api/agents/assign", json={"task_id": "x"})
        # Endpoint raises KeyError for missing profile_id (unhandled -> 500)
        assert resp.status_code >= 400

    def test_assign_agent_full_flow(self, client, app, db, tmp_path):
        """POST /api/agents/assign end-to-end with a real temp git repo."""
        # Create task and profile
        task = Task(
            id="task-api-assign",
            title="API assign test",
            type="todo",
            status="new",
        )
        profile = AgentProfile(
            id="agent-api-assign",
            display_name="API Bot.exe",
            avatar="shiba",
        )
        db.session.add_all([task, profile])
        db.session.commit()

        repo_path = _make_temp_git_repo(tmp_path)

        resp = client.post("/api/agents/assign", json={
            "task_id": "task-api-assign",
            "profile_id": "agent-api-assign",
            "repo_path": repo_path,
            "use_worktree": False,  # branch-only is simpler in tests
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["task"]["status"] == "in_progress"
        assert data["agent"]["id"] == "agent-api-assign"
        assert data["worktree"]["status"] == "ready"

    def test_agent_inbox_send_and_receive(self, client, app, db):
        """POST then GET agent inbox messages."""
        resp = client.post("/api/agents/task-inbox-1/inbox", json={
            "content": "Please hurry up!",
            "sender": "user",
        })
        assert resp.status_code == 201

        resp = client.get("/api/agents/task-inbox-1/inbox")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1
        assert data[0]["content"] == "Please hurry up!"

    def test_agent_outbox_creates_feedback_signal(self, client, app, db):
        """Agent sending feedback message creates a Signal automatically."""
        task = Task(
            id="task-outbox-1", title="Outbox test", extra={"repo": "org/repo"},
        )
        db.session.add(task)
        db.session.commit()

        resp = client.post("/api/agents/task-outbox-1/outbox", json={
            "content": "Use consistent error codes",
            "sender": "agent",
            "message_type": "feedback",
            "metadata": {
                "feedback_category": "error_handling",
                "feedback_severity": "suggestion",
            },
        })
        assert resp.status_code == 201

        sig = Signal.query.filter_by(
            source_type="session_feedback",
            category="error_handling",
        ).first()
        assert sig is not None
        assert "error codes" in sig.text

    def test_agent_messages_full_conversation(self, client, app, db):
        """GET /api/agents/<task>/messages returns both directions."""
        client.post("/api/agents/task-conv-1/inbox", json={
            "content": "Context update", "sender": "brain",
        })
        client.post("/api/agents/task-conv-1/outbox", json={
            "content": "Got it!", "sender": "agent",
        })

        resp = client.get("/api/agents/task-conv-1/messages")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        directions = {m["direction"] for m in data}
        assert directions == {"to_agent", "from_agent"}

    def test_list_agents_returns_prepared(self, client, app, db):
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)


# ---------------------------------------------------------------------------
# Test 10: Skill Results
# ---------------------------------------------------------------------------


class TestSkillResults:

    def test_skill_result_saved_and_retrieved(self, app, db):
        """Verify that SkillResult records can be persisted and queried."""
        sr = SkillResult(
            skill_name="morning-brief",
            title="Morning Brief -- April 1",
            content="Here is your morning summary...",
        )
        db.session.add(sr)
        db.session.commit()

        fetched = SkillResult.query.filter_by(skill_name="morning-brief").first()
        assert fetched is not None
        assert fetched.title == "Morning Brief -- April 1"
        assert "morning summary" in fetched.content

    def test_skill_results_api_list(self, client, app, db):
        sr = SkillResult(
            skill_name="brainstorm",
            title="Brainstorm -- April 1",
            content="Ideas: ...",
        )
        db.session.add(sr)
        db.session.commit()

        resp = client.get("/api/skill-results")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) >= 1

    def test_skill_results_api_filter_by_name(self, client, app, db):
        sr1 = SkillResult(skill_name="brief", title="B1", content="...")
        sr2 = SkillResult(skill_name="investigate", title="I1", content="...")
        db.session.add_all([sr1, sr2])
        db.session.commit()

        resp = client.get("/api/skill-results?skill_name=brief")
        data = resp.get_json()
        assert all(r["skill_name"] == "brief" for r in data)


# ---------------------------------------------------------------------------
# Test 11: Agent Monitor (auto-complete tasks)
# ---------------------------------------------------------------------------


class TestAgentMonitor:

    def test_process_agent_done_completes_task(self, app, db):
        task = Task(id="task-mon-1", title="Monitor test", status="in_progress")
        db.session.add(task)

        pup = Pupdate(
            id="pup-agent-done-1",
            source="agent",
            type="agent_done",
            title="Agent finished task-mon-1",
            tags=["task-mon-1"],
            brain_processed=False,
        )
        db.session.add(pup)
        db.session.commit()

        from planet_maiko.agents.monitor import process_agent_pupdates
        result = process_agent_pupdates()
        assert result["completed_tasks"] == 1

        refreshed = db.session.get(Task, "task-mon-1")
        assert refreshed.status == "done"

    def test_agent_activity_tracks_recent(self, app, db):
        pup = Pupdate(
            id="pup-activity-1", source="agent", type="agent_update",
            title="Working on tests", tags=["task-act-1"],
        )
        db.session.add(pup)
        db.session.commit()

        from planet_maiko.agents.monitor import get_agent_activity
        activity = get_agent_activity()
        assert len(activity) >= 1
        assert activity[0]["task_id"] == "task-act-1"


# ---------------------------------------------------------------------------
# Test 12: Prepare() with real git repo
# ---------------------------------------------------------------------------


class TestPrepareIntegration:

    def test_prepare_branch_mode(self, app, db, tmp_path):
        """prepare() in branch mode creates branch, writes files, creates pupdate."""
        repo_path = _make_temp_git_repo(tmp_path)

        from planet_maiko.agents.coding_agent import prepare
        result = prepare(
            task_id="task-prep-1",
            task_title="Prep integration test",
            prompt="Implement the widget feature",
            repo_path=repo_path,
            use_worktree=False,
        )

        assert result is not None
        assert result["status"] == "ready"
        assert result["mode"] == "branch"
        assert result["branch"].startswith("maiko/")

        # Verify TASK.md exists
        task_md = os.path.join(result["working_path"], "TASK.md")
        assert os.path.exists(task_md)
        with open(task_md, encoding="utf-8") as f:
            assert "task-prep-1" in f.read()

        # Verify CLAUDE.md exists
        claude_md = os.path.join(result["working_path"], "CLAUDE.md")
        assert os.path.exists(claude_md)
        with open(claude_md, encoding="utf-8") as f:
            content = f.read()
        assert "Planet Maiko" in content
        assert "maiko report" in content

        # Verify notification pupdate was created
        notify = Pupdate.query.filter_by(type="agent_ready").first()
        assert notify is not None
        assert "Prep integration test" in notify.title


# ---------------------------------------------------------------------------
# Test 13: Specialization scoring in recommendations
# ---------------------------------------------------------------------------


class TestRecommendationScoring:

    def test_experienced_agent_recommended_first(self, app, db):
        experienced = AgentProfile(
            id="agent-exp-rec", display_name="Exp Bot", avatar="shiba",
            tasks_completed=15, tasks_failed=2,
            specializations={"org/repo:testing": 0.9, "org/repo:style": 0.7},
        )
        newbie = AgentProfile(
            id="agent-new-rec", display_name="New Bot", avatar="corgi",
            tasks_completed=0, tasks_failed=0,
        )
        db.session.add_all([experienced, newbie])
        db.session.commit()

        from planet_maiko.agents.profiles import recommend_agent
        recs = recommend_agent(repo="org/repo", categories=["testing"])

        profiled = [r for r in recs if r.get("profile")]
        assert profiled[0]["profile"]["id"] == "agent-exp-rec"

    def test_gap_inserted_when_all_below_threshold(self, app, db):
        weak = AgentProfile(
            id="agent-weak-rec", display_name="Weak Bot", avatar="shiba",
            tasks_completed=0, tasks_failed=5,
        )
        db.session.add(weak)
        db.session.commit()

        from planet_maiko.agents.profiles import recommend_agent
        recs = recommend_agent(repo="unknown-repo", categories=["security"])
        assert any(r.get("gap_detected") for r in recs)


# ---------------------------------------------------------------------------
# Test 15: Pupdate Correlator
# ---------------------------------------------------------------------------


class TestCorrelator:

    def test_correlator_runs_without_error(self, app, db):
        """Correlator should run cleanly even with no matching patterns."""
        pup = Pupdate(
            id="pup-corr-1", source="github", type="pr_ci_failed",
            title="CI failed", priority="high",
        )
        db.session.add(pup)
        db.session.commit()

        from planet_maiko.brain.pupdates.correlator import correlate
        result = correlate()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Test 16: Custom Skills CRUD
# ---------------------------------------------------------------------------


class TestCustomSkills:

    def test_create_and_list_skills(self, client, app, db):
        resp = client.post("/api/skills", json={
            "id": "test-skill",
            "name": "Test Skill",
            "description": "A test skill",
            "prompt": "Do the thing: {tasks}",
            "icon": "zap",
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["id"] == "test-skill"

        resp = client.get("/api/skills")
        assert resp.status_code == 200
        skills = resp.get_json()
        ids = [s["id"] for s in skills]
        assert "test-skill" in ids

    def test_update_skill(self, client, app, db):
        skill = CustomSkill(
            id="skill-upd", name="Updatable",
            prompt="Old prompt", is_default=False,
        )
        db.session.add(skill)
        db.session.commit()

        resp = client.patch("/api/skills/skill-upd", json={"prompt": "New prompt"})
        assert resp.status_code == 200
        assert resp.get_json()["prompt"] == "New prompt"

    def test_delete_non_default_skill(self, client, app, db):
        skill = CustomSkill(
            id="skill-del", name="Deletable",
            prompt="Delete me", is_default=False,
        )
        db.session.add(skill)
        db.session.commit()

        resp = client.delete("/api/skills/skill-del")
        assert resp.status_code == 200

    def test_cannot_delete_default_skill(self, client, app, db):
        skill = CustomSkill(
            id="skill-default", name="Default",
            prompt="Cant delete", is_default=True,
        )
        db.session.add(skill)
        db.session.commit()

        resp = client.delete("/api/skills/skill-default")
        assert resp.status_code == 400
