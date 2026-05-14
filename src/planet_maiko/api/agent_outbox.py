"""Per-message-type handlers for /agents/<task_id>/outbox.

The original `agent_sends_message` route was a 410-line switchboard
on `data["message_type"]`, with another 125-line helper
(`_handle_agent_job_reply`) running the AgentJob equivalent. Pulled
the per-type bodies into named helpers here so the route in
agents_api.py becomes a thin dispatcher and each handler is
testable / readable on its own.

Conventions:
  - Each handler takes (task_id, task, msg, data) or
    (job, msg, data) and returns None.
  - Handlers mutate db.session via add()/attribute assignment.
    The caller commits once at the end.
  - Errors are logged but never raised — outbox is best-effort
    so a half-failed parse on one block doesn't lose the rest of
    the message.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage


# Job kinds whose worktree the user can come back to and act on after
# the agent posts ready_for_review:
#   coding                 — agent committed locally; user reviews the
#                            diff, approves, then the SAME agent (in the
#                            same worktree) pushes + opens the PR. If
#                            we cleaned the worktree on ready_for_review
#                            the diff would be lost and the approve flow
#                            would have nothing to push. The pr_merged
#                            automation cleans up after the PR lands.
#   review / pr_review     — user iterates with PATTERN / PROPOSAL
#                            discussions, asks follow-ups via wake_agent.
#   investigation /        — user often has follow-up questions on the
#     repo_analysis         report; wake fails if the worktree is gone.
#   cartograph             — repo walk is useful state to query against.
# wake_agent resumes the claude session against this worktree, so
# cleanup waits until either the user is truly done (PR merged, task
# closed, shutdown age-out at 7 days) or an explicit forget call.
FOLLOWUP_KINDS = {
    "coding",
    "review", "pr_review",
    "investigation", "repo_analysis",
    "cartograph",
}


# Message types that signal "the agent is done with its current turn"
# — the runtime can tear down any per-turn state it was holding open
# (e.g. tmux-pane-based runtimes kill the pane here). Anything not in
# this set is mid-flight chatter and leaves the session alone.
_TERMINAL_MESSAGE_TYPES = {
    "ready_for_review",
    "stuck",
    "plan_for_approval",
    "pr_opened",
}


def _maybe_end_runtime_session(job_id, message_type):
    """Tell the active runtime to tear down the agent's current turn,
    if ``message_type`` is terminal. No-op for runtimes (like the
    headless ClaudeCodeRuntime) whose subprocess exits on its own at
    the end of a turn.

    Fans out end_session across every already-instantiated runtime.
    Each runtime's end_session is idempotent — claude-code's is a
    no-op, tmux only kills its own bound session for this job_id.
    Fan-out means we can't miss a teardown if the agent was kicked
    off under one runtime (e.g. tmux via per-task routing) but the
    default runtime resolves to another (e.g. claude-code). Letting
    the tmux pane leak strands the kickoff thread on its
    _wait_for_session_end and the next follow-up message ends up
    behind a held lock.
    """
    if message_type not in _TERMINAL_MESSAGE_TYPES:
        return
    try:
        from planet_maiko.agents import brain_session
        for name, runtime in list(brain_session._runtimes.items()):
            if runtime is None:
                continue
            try:
                runtime.end_session(job_id)
            except Exception as e:
                logger.warning(f"[outbox] {name}.end_session({job_id}) failed: {e}")
    except Exception as e:
        logger.warning(f"[outbox] runtime fan-out for {job_id} failed: {e}")

logger = logging.getLogger(__name__)


_VALID_REVIEW_VERDICTS = {"approve", "approve_with_comments", "soft_block", "hard_block"}


def parse_verdict_and_summary(content):
    """Pull the required `VERDICT:` + `SUMMARY:` lines out of a review
    agent's ready_for_review body.

    Protocol says the first two non-blank lines of the content are:

        VERDICT: approve | approve_with_comments | soft_block | hard_block
        SUMMARY: <one or two sentences>

    Case-insensitive on the label; tolerates extra whitespace. Returns
    (verdict, summary) — either value can be None when the tag was
    absent or malformed. An unknown verdict keyword is dropped too, so
    the stored value is always one of the enum or None.

    We don't fail the ready_for_review on missing verdict — old-shape
    reviews that only produce a long prose body still succeed (the
    artifact is preserved); the banner just won't have anything to
    show until the agent produces a new one in the new shape.
    """
    verdict = None
    summary = None
    if not content:
        return verdict, summary
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r"^verdict\s*:\s*(\S+)", stripped, re.IGNORECASE)
        if m and verdict is None:
            candidate = m.group(1).strip().lower()
            if candidate in _VALID_REVIEW_VERDICTS:
                verdict = candidate
            continue
        m = re.match(r"^summary\s*:\s*(.+)$", stripped, re.IGNORECASE)
        if m and summary is None:
            summary = m.group(1).strip()[:500]
            continue
        # Stop once we've passed the header and hit a line that isn't
        # a known tag — SUMMARY can be continued on the next line but
        # anything else ends the search.
        if verdict is not None and summary is not None:
            break
    return verdict, summary


# ============================================================
# AgentJob path — Stage D one-shots
# ============================================================

def handle_agent_job_reply(job, msg, data, message_type):
    """Route a Stage-D AgentJob reply to the right handler.

    Job-typed runs (review / cartograph / investigation / specialty)
    finish via this path; the route already saved the AgentMessage.
    """
    from planet_maiko.models.agent_job import AgentJob as _AgentJob  # noqa: F401
    from planet_maiko.models.task import Task as _Task
    from planet_maiko.models.agent_profile import AgentProfile as _AP
    from planet_maiko.brain.learning.agent_output import parse_and_apply_blocks

    content = data.get("content") or ""

    if message_type == "ready_for_review":
        verdict, summary = parse_verdict_and_summary(content)
        extra = dict(job.extra or {})
        if verdict:
            extra["review_verdict"] = verdict
        if summary:
            extra["review_summary"] = summary

        ag = db.session.get(_AP, job.agent_profile_id) if job.agent_profile_id else None
        # Pass the linked Task (when one exists) into the parser so
        # PROPOSAL: / TASK: blocks become agent_proposal memos. The
        # parser guards on `agent is not None and task is not None`,
        # so task=None silently dropped every follow-up the agent
        # emitted -- the most user-facing thing an investigation does.
        source_task = (
            db.session.get(_Task, job.source_task_id)
            if job.source_task_id else None
        )
        try:
            parsed = parse_and_apply_blocks(
                content, agent=ag, task=source_task, repo=job.scope_repo,
            )
            cleaned = parsed.get("cleaned_output", content)
            job.artifact = cleaned[:16000]
            extra["patterns_emitted"] = parsed.get("patterns_emitted", 0)
            extra["proposals_emitted"] = parsed.get("proposals_emitted", 0)
            if parsed.get("confidence"):
                extra["confidence"] = parsed["confidence"]
        except Exception as e:
            logger.warning(f"[outbox/job] artifact save failed for {job.id}: {e}")
            job.artifact = content[:16000]

        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        job.extra = extra
        if ag:
            ag.last_active_at = datetime.now(timezone.utc)
            # Post-Stage D, most work finishes as an AgentJob (not a
            # Task), so AgentProfile.tasks_completed stopped moving —
            # the number in the profile modal was frozen wherever the
            # legacy one-shot Task path last left it. Bump here so the
            # "done" stat reflects reality.
            ag.tasks_completed = (ag.tasks_completed or 0) + 1

        # Sync the linked Task. The agent's ready_for_review moves the
        # task into "review" status regardless of kind — only the user
        # can mark it "done" via /tasks/<id>/done. Why: setting "done"
        # here triggered shutdown-ritual + complete_task worktree
        # cleanup, and the user kept losing the working dir before
        # they'd finished reviewing the agent's output.
        if job.source_task_id:
            t = db.session.get(_Task, job.source_task_id)
            if t:
                task_extra = dict(t.extra or {})
                if verdict:
                    task_extra["review_verdict"] = verdict
                if summary:
                    task_extra["review_summary"] = summary
                task_extra["artifact"] = job.artifact
                task_extra["completed_at"] = datetime.now(timezone.utc).isoformat()
                # Mirror the parsed-block metadata too. These keys come
                # from parse_and_apply_blocks; copy whichever the job
                # picked up so the Task surface tells the same story.
                for key in ("patterns_emitted", "proposals_emitted",
                            "confidence", "rules_considered"):
                    if key in extra:
                        task_extra[key] = extra[key]
                t.extra = task_extra
                t.status = "review"

        # Worktrees no longer get torn down here. The agent saying
        # "ready_for_review" is a *suggestion* the user accepts via
        # /tasks/<id>/done (which cleans up), reassign (which re-preps),
        # or forget. Tasks that linger past their welcome are caught by
        # the shutdown ritual's age-out sweep — see
        # planet_maiko.agents.runtime.worktree.sweep_old_worktrees.

        # Emit the user-facing memo. The Task path does this via
        # emit_user_facing_signal but the AgentJob branch returns early
        # before that helper runs, so without this call review /
        # investigation / cartograph completions never landed in the
        # user's inbox — they only showed up on Active Agents and
        # /jobs/<id>. The protocol tells agents NOT to set
        # recipient="user" on ready_for_review specifically because
        # "the surface already knows the user should see them"; this
        # is the surface knowing.
        try:
            from planet_maiko.brain.memos import create_memo
            agent_name = (ag.display_name if ag else None) or f"{job.kind} agent"
            preview = (content or "").replace("\n", " ").strip()
            if len(preview) > 80:
                preview = preview[:77] + "…"

            # Every memo CTA points at the unified /jobs/<id> page now.
            # The page renders the right section based on ?view=<...>:
            #   diff-producing kinds (coding, review, pr_review) →
            #     ?view=diff (DiffPanel)
            #   report-producing kinds (investigation, repo_analysis,
            #     cartograph) → ?view=report (ReportPanel)
            # The legacy /tasks/<id>/review URL still resolves through
            # TaskRouteRedirect, but new memos route directly so the
            # user lands without the redirect hop.
            is_report_kind = job.kind in ("investigation", "repo_analysis", "cartograph")
            if not is_report_kind:
                cta_label = "Review diff"
                cta_action = "review"
                memo_url = f"/jobs/{job.id}?view=diff"
            else:
                cta_label = "View report"
                cta_action = "open"
                memo_url = f"/jobs/{job.id}?view=report"
            type_label = "ready for review"
            priority = "high"
            if extra.get("confidence") == "low":
                priority = "normal"

            create_memo(
                kind="agent_ready",
                category="waiting",
                title=f"{agent_name} {type_label}: {preview}",
                body=content,
                url=memo_url,
                cta_label=cta_label,
                cta_action=cta_action,
                priority=priority,
                source_agent_id=job.agent_profile_id,
                source_task_id=job.source_task_id or job.id,
                extra={
                    "task_id": job.source_task_id or job.id,
                    "job_id": job.id,
                    "agent_id": job.agent_profile_id,
                    "message_type": "ready_for_review",
                    "review_url": memo_url,
                    "verdict": verdict,
                    "summary": summary,
                    "confidence": extra.get("confidence"),
                },
            )
        except Exception as e:
            logger.warning(f"[outbox/job] memo emission failed for {job.id}: {e}")

        # Signal the runtime to tear down any per-turn state (kills
        # the tmux pane on TmuxClaudeRuntime; no-op on the headless
        # ClaudeCodeRuntime whose subprocess exited on its own).
        _maybe_end_runtime_session(job.id, message_type)

        logger.info(f"[outbox/job] {job.kind} job {job.id} done")
        return

    if message_type == "insight":
        try:
            from planet_maiko.models.insight import Insight, find_duplicate
            ag = db.session.get(_AP, job.agent_profile_id) if job.agent_profile_id else None
            author_role = ag.role if ag else None
            is_cartographer = author_role == "cartographer" or job.kind == "cartograph"
            tags = list(data.get("tags") or [])
            if is_cartographer:
                for t_ in ("overview", "cartographer"):
                    if t_ not in tags:
                        tags.append(t_)
            max_len = 8000 if is_cartographer else 2000
            text = content.strip()[:max_len]
            existing = find_duplicate(text, job.scope_repo, tags)
            if existing is not None:
                existing.last_confirmed_at = datetime.now(timezone.utc)
                existing.source_message_id = msg.id
                merged_tags = list(existing.tags or [])
                for t_ in tags:
                    if t_ not in merged_tags:
                        merged_tags.append(t_)
                existing.tags = merged_tags
                logger.info(
                    f"[outbox/job] insight dedup match on #{existing.id} "
                    f"(repo={job.scope_repo or 'global'}) — refreshed"
                )
            else:
                ins = Insight(
                    text=text,
                    repo_scope=job.scope_repo,
                    tags=tags,
                    author_agent_id=job.agent_profile_id,
                    status="pending",
                    source_message_id=msg.id,
                )
                db.session.add(ins)
                logger.info(
                    f"[outbox/job] insight from {job.id} "
                    f"(repo={job.scope_repo or 'global'}, tags={tags})"
                )
        except Exception as e:
            logger.warning(f"[outbox/job] insight save failed for {job.id}: {e}")
        return

    if message_type == "feedback":
        # Mirrors handle_session_feedback on the Task path. Without
        # this branch, agents in unified-path sessions could submit
        # reply(message_type="feedback") and the AgentMessage was
        # recorded but no Signal got written. The pack-insights
        # "Keep" flow then had nothing to keep, and the campfire
        # buttons appeared to do nothing.
        try:
            from planet_maiko.models.signal import Signal
            metadata = data.get("metadata") or {}
            category = metadata.get("feedback_category", "pattern")
            severity = metadata.get("feedback_severity", "suggestion")
            recent = (
                AgentMessage.query
                .filter_by(task_id=job.id, direction="from_agent")
                .order_by(AgentMessage.created_at.desc())
                .first()
            )
            code_context = recent.content[:3000] if recent else None
            signal = Signal(
                category=category,
                text=content,
                source_type="session_feedback",
                severity=severity,
                repo=job.scope_repo,
                file_path=metadata.get("file_path"),
                code_context=code_context,
                # The agent provided an explicit category; skip the
                # synthesizer's re-classification step.
                synthesized=True,
                source_message_id=msg.id,
            )
            db.session.add(signal)
            logger.info(
                f"[outbox/job] feedback from {job.id} "
                f"(repo={job.scope_repo or 'global'}, category={category})"
            )
        except Exception as e:
            logger.warning(f"[outbox/job] feedback save failed for {job.id}: {e}")
        return

    if message_type == "stuck":
        # Same log-only treatment as the Task path — the AgentMessage
        # row is enough to surface the stuck status in the UI.
        logger.info(f"[outbox/job] {job.id} stuck: {content[:100]}")
        _maybe_end_runtime_session(job.id, message_type)
        return

    # Anything else (status / summary / message) — just records the
    # AgentMessage row we already added, nothing further.


# ============================================================
# Task path — pre-Stage-D code path, plus coding tasks that
# still finish here.
# ============================================================

def handle_task_ready_for_review(task_id, task, data):
    """Parse the agent's ready_for_review body, save artifact + verdict
    onto task.extra, and move the task into "review" status awaiting
    the user's verdict.

    Worktrees aren't torn down here. The agent saying ready_for_review
    is a *suggestion* — the user accepts (and triggers cleanup) by
    clicking Done in the UI, reassigning, or forgetting. Aged-out
    worktrees still get swept by the shutdown ritual.
    """
    from planet_maiko.models.agent_profile import AgentProfile as _AgentProfile
    from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE
    from planet_maiko.brain.learning.agent_output import parse_and_apply_blocks

    if not task:
        return

    # Parse VERDICT + SUMMARY for any ready_for_review, not just
    # review tasks. Coding agents self-assess the same way — the
    # banner shows the self-verdict (approve / approve_with_comments
    # / soft_block / hard_block) at the top of their diff page.
    verdict, summary = parse_verdict_and_summary(data.get("content") or "")
    if verdict or summary:
        extra = dict(task.extra or {})
        if verdict:
            extra["review_verdict"] = verdict
        if summary:
            extra["review_summary"] = summary
        task.extra = extra

    if task.type not in ONE_SHOT_ROLE_FOR_TYPE:
        return

    ag = db.session.get(_AgentProfile, task.assigned_agent_id) if task.assigned_agent_id else None
    try:
        parsed = parse_and_apply_blocks(
            data["content"], agent=ag, task=task,
            repo=(task.extra or {}).get("repo"),
        )
        cleaned = parsed.get("cleaned_output", data["content"])
        extra = dict(task.extra or {})
        extra["artifact"] = cleaned[:16000]
        extra["patterns_emitted"] = parsed.get("patterns_emitted", 0)
        extra["proposals_emitted"] = parsed.get("proposals_emitted", 0)
        if parsed.get("confidence"):
            extra["confidence"] = parsed["confidence"]

        extra["completed_at"] = datetime.now(timezone.utc).isoformat()
        task.extra = extra
        task.status = "review"

        if ag:
            ag.last_active_at = datetime.now(timezone.utc)
        logger.info(
            f"[outbox] {task.type} task {task_id} ready for review — "
            f"{parsed.get('patterns_emitted', 0)} patterns, "
            f"{parsed.get('proposals_emitted', 0)} proposals"
        )
    except Exception as e:
        logger.warning(f"[outbox] artifact save failed for {task_id}: {e}")


def handle_task_insight(task_id, task, msg, data):
    """Agent-reported insight: tribal / operational knowledge that
    should be injected into future agents' CLAUDE.md. Lands as a
    pending Insight so the user reviews before it goes into every new
    session's prompt. Cartographer replies are auto-tagged
    overview/cartographer and given more length headroom.
    """
    try:
        from planet_maiko.models.insight import Insight, find_duplicate
        from planet_maiko.models.agent_profile import AgentProfile as _AP
        repo_scope = None
        if task:
            extra = task.extra or {}
            repo_scope = extra.get("repo") or extra.get("repository")

        author_role = None
        if task and task.assigned_agent_id:
            author = db.session.get(_AP, task.assigned_agent_id)
            if author:
                author_role = author.role
        is_cartographer = author_role == "cartographer"

        tags = list(data.get("tags") or [])
        if is_cartographer:
            for t_ in ("overview", "cartographer"):
                if t_ not in tags:
                    tags.append(t_)

        max_len = 8000 if is_cartographer else 2000
        text = (data["content"] or "").strip()[:max_len]
        existing = find_duplicate(text, repo_scope, tags)
        if existing is not None:
            existing.last_confirmed_at = datetime.now(timezone.utc)
            existing.source_message_id = msg.id
            merged_tags = list(existing.tags or [])
            for t_ in tags:
                if t_ not in merged_tags:
                    merged_tags.append(t_)
            existing.tags = merged_tags
            logger.info(
                f"[outbox] insight dedup match on #{existing.id} "
                f"(task={task_id}, repo={repo_scope or 'global'}) — refreshed"
            )
        else:
            ins = Insight(
                text=text,
                repo_scope=repo_scope,
                tags=tags,
                author_agent_id=(task.assigned_agent_id if task else None),
                status="pending",
                source_message_id=msg.id,
            )
            db.session.add(ins)
            logger.info(
                f"[outbox] Agent insight recorded (task={task_id}, "
                f"repo={repo_scope or 'global'}, tags={tags}): {ins.text[:80]}"
            )
    except Exception as e:
        logger.warning(f"[outbox] insight save failed for {task_id}: {e}")


def emit_user_facing_signal(task_id, task, msg, data, message_type):
    """For user-acting message types (ready_for_review, stuck,
    plan_for_approval, etc.), create either a Memo (the canonical
    surface) or a fallback Pupdate carrying agent_name + preview +
    action hint. Skipped for status/feedback/insight/summary which
    don't need a user-visible signal.
    """
    if message_type in ("status", "feedback", "insight", "summary"):
        return

    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile

    agent_name = None
    if task and task.assigned_agent_id:
        agent = db.session.get(AgentProfile, task.assigned_agent_id)
        if agent:
            agent_name = agent.display_name
    agent_name = agent_name or "Agent"

    content = data["content"]
    preview = content.replace("\n", " ").strip()
    if len(preview) > 80:
        preview = preview[:77] + "…"

    # Priority: stuck is high (blocked, needs help);
    # ready_for_review / plan_for_approval are high (user needs to act);
    # plain messages are normal.
    priority = "high" if message_type in ("stuck", "ready_for_review", "plan_for_approval") else "normal"
    type_label = {
        "done": "completed",
        "stuck": "is stuck",
        "ready_for_review": "ready for review",
        "plan_for_approval": "has a plan",
        "pr_opened": "opened PR",
        "message": "replied",
    }.get(message_type, "replied")

    pupdate_type = {
        "ready_for_review": "agent_ready_for_review",
        "plan_for_approval": "agent_plan_for_approval",
        "pr_opened": "agent_pr_opened",
        "done": "agent_done",
        "stuck": "agent_stuck",
    }.get(message_type, "agent_message")

    action_hint = {
        "ready_for_review": "Review diff",
        "plan_for_approval": "Review plan",
        "pr_opened": "Open PR",
        "stuck": "Help the agent",
        "done": "Open task",
    }.get(message_type, "Open task")

    # Pull the PR URL off the task (if the agent has opened one)
    # so the pupdate card can link straight to GitHub.
    pr_url = None
    if task:
        candidate = task.url or (task.extra or {}).get("pr_url")
        if candidate and "github.com" in candidate:
            pr_url = candidate

    # Carry forward the original ask + boundary + when it was asked
    # so the user can reload context in one glance.
    original_ask = ""
    original_non_goals = ""
    asked_at_iso = None
    if task is not None:
        t_extra = task.extra or {}
        original_ask = (
            t_extra.get("user_request")
            or t_extra.get("description")
            or t_extra.get("body")
            or task.title
            or ""
        ).strip()
        raw_ng = t_extra.get("non_goals") or ""
        if isinstance(raw_ng, list):
            original_non_goals = "; ".join(str(g).strip() for g in raw_ng if str(g).strip())
        else:
            original_non_goals = str(raw_ng).strip()
        if task.created_at:
            asked_at_iso = task.created_at.isoformat() if hasattr(task.created_at, "isoformat") else str(task.created_at)

    # User-gated signals (ready_for_review / stuck / plan_for_approval)
    # become Memos. Other types stay as pupdates for now.
    memo_kind = {
        "ready_for_review": "agent_ready",
        "stuck": "agent_stuck",
        "plan_for_approval": "agent_plan",
    }.get(message_type)

    if memo_kind:
        from planet_maiko.brain.memos import create_memo
        from planet_maiko.models.agent_job import AgentJob as _AgentJob
        # Report-producing one-shot roles (investigation, repo_analysis)
        # finish with no diff — worktree is wiped and the artifact lives
        # All memo CTAs route into the unified /jobs/<id> page now.
        # ?view selects the right section. Old /tasks/<id>/{review,plan,
        # report} URLs still resolve via the TaskRouteRedirect bridge,
        # but emitting the canonical job URL upfront skips the
        # redirect hop.
        task_type = task.type if task else None
        is_report_task = task_type in ("investigation", "repo_analysis")
        linked_job = (
            _AgentJob.query
            .filter_by(source_task_id=task_id)
            .filter(_AgentJob.status != "cancelled")
            .order_by(_AgentJob.created_at.desc())
            .first()
        )
        view_for_kind = {
            "agent_ready": "report" if is_report_task else "diff",
            "agent_plan": "plan",
        }.get(memo_kind)
        if memo_kind == "agent_stuck":
            # Stuck routes to the job's chat panel so the user can
            # reply in-context. Falls back to /agents when there's no
            # linked job (legacy task with no AgentJob).
            if linked_job:
                cta = ("Open chat", "open", f"/jobs/{linked_job.id}?view=chat")
            else:
                cta = ("Help out", "open", "/agents")
        elif linked_job and view_for_kind:
            label = "View report" if view_for_kind == "report" else (
                "Review plan" if view_for_kind == "plan" else "Review diff"
            )
            cta_action = "open" if view_for_kind == "report" else "review"
            cta = (label, cta_action, f"/jobs/{linked_job.id}?view={view_for_kind}")
        else:
            # No linked job (legacy task with no AgentJob) — fall
            # back to the legacy task route so the redirect bridge
            # at least lands the user somewhere.
            cta = {
                "agent_ready": (
                    "View report" if is_report_task else "Review diff",
                    "open" if is_report_task else "review",
                    f"/tasks/{task_id}/{'report' if is_report_task else 'review'}",
                ),
                "agent_plan": ("Review plan", "review", f"/tasks/{task_id}/plan"),
            }[memo_kind]
        create_memo(
            kind=memo_kind,
            category="waiting",
            title=f"{agent_name} {type_label}: {preview}",
            body=content,
            url=pr_url or cta[2],
            cta_label=cta[0],
            cta_action=cta[1],
            priority=priority,
            source_agent_id=task.assigned_agent_id if task else None,
            source_task_id=task_id,
            extra={
                "task_id": task_id,
                "agent_id": task.assigned_agent_id if task else None,
                "message_type": message_type,
                "pr_url": pr_url,
                "original_ask": original_ask[:500],
                "original_non_goals": original_non_goals[:500] if original_non_goals else "",
                "asked_at": asked_at_iso,
                "review_url": cta[2],
            },
        )
    else:
        from planet_maiko.models.pupdate import Pupdate
        pupdate = Pupdate(
            id=f"agent-msg-{task_id}-{uuid.uuid4().hex[:8]}",
            source="maiko",
            source_id=f"agent-msg/{task_id}/{msg.id or uuid.uuid4().hex[:8]}",
            type=pupdate_type,
            priority=priority,
            title=f"{agent_name} {type_label}: {preview}",
            body=content,
            actionable=True,
            action_hint=action_hint,
            url=pr_url,
            tags=[task_id, "agent-message"],
            extra={
                "task_id": task_id,
                "agent_id": task.assigned_agent_id if task else None,
                "message_type": message_type,
                "pr_url": pr_url,
                "original_ask": original_ask[:500],
                "original_non_goals": original_non_goals[:500] if original_non_goals else "",
                "asked_at": asked_at_iso,
            },
            brain_processed=True,
        )
        db.session.add(pupdate)


def handle_pr_opened(task_id, data):
    """Parse the URL out of a pr_opened body and pin it onto the task
    so downstream pieces (pr_review_commented matcher, _complete_review_task
    on merge) can resolve PR comments back to this task.
    """
    from planet_maiko.models.task import Task
    content = data.get("content", "") or ""
    match = re.search(r"https?://[^\s]+", content)
    if not match:
        return
    pr_url = match.group(0).rstrip(".,;")
    task = db.session.get(Task, task_id)
    if not task:
        return
    extra = dict(task.extra or {})
    extra["pr_url"] = pr_url
    task.url = pr_url
    task.extra = extra
    logger.info(f"[outbox] Stored pr_url for {task_id}: {pr_url}")


def handle_session_feedback(task_id, msg, data, get_repo_for_task):
    """Persist a user-feedback Signal with the most recent agent
    output as code context. Called on message_type=feedback.

    `get_repo_for_task(task_id)` is passed in so the helper doesn't
    have to depend on the agents_api module — keeps the import graph
    one-way (agents_api → agent_outbox, never back).
    """
    metadata = data.get("metadata", {})
    category = metadata.get("feedback_category", "pattern")
    severity = metadata.get("feedback_severity", "suggestion")

    recent_agent_msg = (
        AgentMessage.query
        .filter_by(task_id=task_id, direction="from_agent")
        .order_by(AgentMessage.created_at.desc())
        .first()
    )
    code_context = recent_agent_msg.content[:3000] if recent_agent_msg else None

    from planet_maiko.models.signal import Signal
    signal = Signal(
        category=category,
        text=data["content"],
        source_type="session_feedback",
        severity=severity,
        repo=get_repo_for_task(task_id),
        file_path=metadata.get("file_path"),
        code_context=code_context,
        # Session feedback carries an explicit category from the agent
        # — skip re-synthesis.
        synthesized=True,
        source_message_id=msg.id,
    )
    db.session.add(signal)
