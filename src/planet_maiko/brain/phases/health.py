"""Agent + task health phases.

  - nudge_quiet_agents: heartbeat-wake running agents that have gone
    silent — gives them a chance to re-engage before stuck_check flags.
  - stuck_check: flag agents whose claude process exited silently
  - stuck_escalation: surface tasks stuck in_progress for too long
  - worktree_sweep: daily-cadenced cleanup of stale agent worktrees
"""

import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


# How long an AgentJob in status="running" can go without its agent
# emitting any pupdate / message before we send a heartbeat-wake. Tuned
# below STUCK_AFTER_MINUTES (15) so quiet agents get one nudge cycle to
# re-engage before the next stuck-check flags them as broken.
NUDGE_AFTER_MINUTES = 10

# Worktree sweep is gated to once-per-day. The brain cycle runs every
# ~minute; we don't want to walk every repo's .maiko-worktrees and
# stat every scratch dir that often. Module-level so a process restart
# resets the clock and a fresh boot does an initial sweep — desirable
# since long-uptime processes are the case where dirs accumulate
# unnoticed.
_last_worktree_sweep_at = 0.0
_WORKTREE_SWEEP_INTERVAL_S = 24 * 60 * 60


def _phase_nudge_quiet_agents():
    """Phase: wake any AgentJob whose agent has been silent for too long.

    Agents are reactive — a claude process exits after each turn and
    only re-runs when wake_agent fires. Without an external trigger
    (user message, automation, this nudge), a job in status="running"
    with no pending input can sit idle indefinitely. This phase pings
    them so their next inbox check happens automatically.

    Skip rules:
      - Lock is held — wake_agent would drop-on-busy anyway, save the
        noise.
      - Agent's most recent message is a "waiting on user" type
        (stuck / plan_for_approval / recipient=user) — they're not
        idle, they're parked waiting for a reply. Don't disturb.
      - last_active_at is fresh (< NUDGE_AFTER_MINUTES) — already
        active in this window.

    wake_agent uses source="heartbeat" so a redundant call against a
    truly-busy agent is silently dropped.
    """
    from flask import current_app
    from planet_maiko.database import db
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.models.agent_message import AgentMessage
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.agents.wake import wake_agent, is_working

    nudged = 0
    skipped_busy = 0
    skipped_waiting = 0
    skipped_fresh = 0
    failed = 0

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=NUDGE_AFTER_MINUTES)
        running = AgentJob.query.filter(AgentJob.status == "running").all()
        for job in running:
            if is_working(job.id):
                skipped_busy += 1
                continue

            # Check the agent's last_active_at via the linked profile.
            # wake_agent.set_agent_state stamps this on each claude
            # subprocess start/end, so it's the canonical heartbeat.
            profile = (
                db.session.get(AgentProfile, job.agent_profile_id)
                if job.agent_profile_id else None
            )
            last_active = profile.last_active_at if profile else None
            if last_active is not None:
                la = last_active
                if la.tzinfo is None:
                    la = la.replace(tzinfo=timezone.utc)
                if la >= cutoff:
                    skipped_fresh += 1
                    continue

            # Skip agents that emitted a "waiting on user" signal — they
            # SHOULD be parked until the user replies, not woken.
            last_msg = (
                AgentMessage.query
                .filter_by(task_id=job.id, direction="from_agent")
                .order_by(AgentMessage.created_at.desc())
                .first()
            )
            if last_msg is not None and (
                last_msg.message_type in ("stuck", "plan_for_approval")
                or (last_msg.recipient or "").lower() == "user"
            ):
                skipped_waiting += 1
                continue

            try:
                ok, _mode = wake_agent(
                    job.id,
                    "Heartbeat. Call check_inbox for any pending messages, "
                    "post a quick status update via "
                    "reply(message_type='status'), and continue your work.",
                    source="heartbeat",
                )
                if ok:
                    nudged += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                logger.warning(f"[nudge] wake failed for job {job.id}: {e}")

        if nudged:
            logger.info(
                f"[nudge] heartbeat-woke {nudged} quiet agent(s) "
                f"(skipped: busy={skipped_busy}, waiting={skipped_waiting}, "
                f"fresh={skipped_fresh})"
            )
    except Exception as e:
        logger.warning(f"[nudge] phase error: {e}")
        return {"nudged": nudged, "error": str(e)}

    return {
        "nudged": nudged,
        "skipped_busy": skipped_busy,
        "skipped_waiting": skipped_waiting,
        "skipped_fresh": skipped_fresh,
        "failed": failed,
    }


def _phase_stuck_check():
    """Phase 5: Flag agents whose claude process exited silently.

    Surfaces an `agent_stuck` pupdate so the user sees it in Pack Requests
    and can decide whether to nudge or abandon. No auto-wake — the wake-
    on-message path handles the case where the user actually wants to
    re-engage.
    """
    try:
        from flask import current_app
        from planet_maiko.agents.wake import check_stuck_agents
        return {"flagged": check_stuck_agents(current_app._get_current_object())}
    except Exception as e:
        logger.warning(f"[cycle] Stuck-agent check error: {e}")
        return {"flagged": 0, "error": str(e)}


def _phase_stuck_escalation():
    """Phase 8d: Surface tasks stuck in_progress for too long as a
    high-priority "needs rescue" pupdate.

    A task in_progress whose updated_at is older than STUCK_DAYS gets a
    single escalation pupdate (dedup by source_id). The user can open
    the task and hit "Reassign" to route it to a different agent. When
    the task is no longer stuck (moved to done/cancelled or updated),
    the escalation pupdate is auto-dismissed.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.database import db

    STUCK_DAYS = 3
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(days=STUCK_DAYS)

    try:
        in_progress = Task.query.filter(Task.status == "in_progress").all()
        escalated = 0
        for t in in_progress:
            updated = t.updated_at
            if updated is None:
                continue
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if updated >= threshold:
                continue

            source_id = f"stuck-task/{t.id}"
            existing = Pupdate.query.filter_by(source_id=source_id).first()
            if existing:
                # Already surfaced once — respect the user's dismissal.
                # If they've decided they know about it, don't keep
                # reminding them every cycle. (And the pupdate ID is
                # fixed at `stuck-{task_id[:32]}`, so a second insert
                # would PK-conflict anyway.) The auto-dismiss pass
                # below still closes the pupdate when the task moves
                # out of in_progress.
                continue

            days = (now - updated).days
            agent_name = (t.assigned_agent_id or "unassigned")
            p = Pupdate(
                id=f"stuck-{t.id[:32]}",
                source="maiko",
                source_id=source_id,
                type="stuck_task",
                priority="high",
                title=f"Stuck task: {t.title}",
                body=f"Task has been in_progress for {days} days with no updates. Assigned to {agent_name}.",
                actionable=True,
                action_hint="Reassign or check in",
                tags=["stuck", t.id, agent_name],
                extra={"task_id": t.id, "days_stuck": days},
            )
            db.session.add(p)
            escalated += 1

        # Auto-dismiss escalations whose task is no longer stuck.
        dismissed = 0
        open_escalations = Pupdate.query.filter_by(type="stuck_task", dismissed=False).all()
        for p in open_escalations:
            task_id = (p.extra or {}).get("task_id")
            if not task_id:
                continue
            task = db.session.get(Task, task_id)
            if not task or task.status != "in_progress":
                # Task completed / cancelled / gone — close the escalation.
                p.dismissed = True
                p.dismissed_at = now
                dismissed += 1

        if escalated or dismissed:
            db.session.commit()
        return {"escalated": escalated, "auto_dismissed": dismissed}
    except Exception as e:
        # Roll back so the leaked pending state from upstream phases
        # (or our own partial writes) doesn't bleed into the cycle's
        # post-phase rollback path. The user was seeing
        # "stuck escalation skipped: raised as a result of query
        # invoked autoflush) UNIQUE constraint failed pupdates.id"
        # spam every cycle because the failing autoflush left the
        # session in a half-committed state across phases.
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning(f"[cycle] Stuck escalation skipped: {e}")
        return {"escalated": 0, "auto_dismissed": 0}


def _phase_worktree_sweep():
    """Phase: daily-cadenced removal of stale agent worktrees.

    Gated by `agents.worktree_cleanup.enabled` (default True) and
    `agents.worktree_cleanup.max_age_days` (default 14). Runs at most
    once per 24h regardless of cycle cadence — module-level timestamp
    tracks last sweep.

    Skipped silently when disabled or when the cooldown hasn't
    elapsed. Errors are logged but never block the cycle.
    """
    global _last_worktree_sweep_at

    try:
        from planet_maiko.config import load_config
        cfg = (load_config().get("agents") or {}).get("worktree_cleanup") or {}
        if not cfg.get("enabled", True):
            return {"skipped": "disabled"}
        max_age_days = int(cfg.get("max_age_days") or 14)
    except Exception as e:
        logger.debug(f"[worktree-sweep] config read failed: {e}")
        return {"skipped": "config_error"}

    now = time.time()
    if (now - _last_worktree_sweep_at) < _WORKTREE_SWEEP_INTERVAL_S:
        return {"skipped": "cooldown"}

    try:
        from planet_maiko.agents.runtime import sweep_old_worktrees
        result = sweep_old_worktrees(max_age_days)
        _last_worktree_sweep_at = now
        if result.get("removed"):
            logger.info(
                f"[cycle] worktree sweep: removed {result['removed']} dir(s), "
                f"freed {result['freed_bytes']} bytes "
                f"(skipped {result.get('skipped_active', 0)} active, "
                f"{result.get('skipped_recent', 0)} recent)"
            )
        return result
    except Exception as e:
        logger.warning(f"[cycle] worktree sweep failed: {e}")
        return {"error": str(e)}
