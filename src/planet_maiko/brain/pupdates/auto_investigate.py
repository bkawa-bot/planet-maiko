"""Auto-spawn investigation agents from correlator incidents.

When the correlator detects an incident pattern (CI fail + rollback +
error spike, etc.) it emits a pupdate with type="incident". This module
decides whether to turn that into an investigation task Maiko runs on
her own, vs. leaving it for the human to triage.

Safety posture: gated behind `brain.auto_investigate.enabled` (default
off) with `dry_run` (default on) as the secondary safety. In dry-run,
the task gets created but no agent kickoff happens — so the user can
watch what *would* have fired during a trial week without burning
tokens. A daily budget halts runaway loops when upstream noise spikes.
"""

import logging
import uuid
from datetime import timezone

from planet_maiko.database import db
from planet_maiko.models.task import Task

logger = logging.getLogger(__name__)


def _count_auto_investigations_today():
    """How many auto-spawned investigations have fired since the user's
    local midnight? Read from the DB so the counter survives server
    restarts — an in-memory counter would reset on every `maiko serve`.
    """
    from planet_maiko.config import user_now

    midnight_local = user_now().replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_utc = midnight_local.astimezone(timezone.utc).replace(tzinfo=None)

    todays = Task.query.filter(
        Task.type == "investigation",
        Task.created_at >= midnight_utc,
    ).all()
    return sum(1 for t in todays if (t.extra or {}).get("auto_spawned"))


def _build_investigation_prompt(task, incident_pupdate):
    """TASK.md content for an auto-spawned investigation.

    Surfaces the incident title, the pattern that triggered it, and the
    IDs of the correlated pupdates so the agent has a trailhead — the
    agent can pull deeper context via MCP or direct DB reads.
    """
    extra = incident_pupdate.extra or {}
    pattern = extra.get("pattern") or []
    correlated = extra.get("correlated_ids") or []
    services = extra.get("services") or []

    lines = [f"# Investigation: {incident_pupdate.title}", ""]
    if pattern:
        lines.append(f"Auto-detected pattern: {' + '.join(pattern)}")
    if services:
        lines.append(f"Affected service(s): {', '.join(services)}")
    lines.extend(["", "## What the correlator saw", ""])
    lines.append(incident_pupdate.body or "_(no body — inspect the correlated events directly)_")
    if correlated:
        lines.extend(["", "## Correlated pupdate IDs"])
        lines.extend(f"- `{pid}`" for pid in correlated)
    lines.extend([
        "",
        "## What to do",
        "",
        "Follow the investigation-agent-protocol in CLAUDE.md. Produce a",
        "written investigation report covering root-cause hypothesis,",
        "impact assessment, recommended mitigations, and a confidence",
        "rating. This worktree is read-only to you — you are diagnosing,",
        "not fixing.",
    ])
    return "\n".join(lines)


def maybe_auto_investigate(incident_pupdate):
    """Consider auto-spawning an investigation agent for this incident.

    Returns the created Task, or None if skipped (disabled, over budget,
    no resolvable repo, etc.). Never raises — correlator calls this
    inside a try/except so it can't take the pupdate pipeline down.
    """
    from planet_maiko.config import load_config

    auto_cfg = ((load_config().get("brain") or {}).get("auto_investigate") or {})
    if not auto_cfg.get("enabled", False):
        return None

    budget = int(auto_cfg.get("daily_budget", 5))
    count_today = _count_auto_investigations_today()
    if count_today >= budget:
        logger.warning(
            f"[auto-investigate] Daily budget reached ({count_today}/{budget}); "
            f"skipping incident {incident_pupdate.id}"
        )
        return None

    services = (incident_pupdate.extra or {}).get("services") or []
    scope_repo = services[0] if services else None

    # Without a resolvable repo there's nowhere to spin up a worktree,
    # so we can't kick an agent off. Still log it — a user seeing these
    # log lines knows to add the repo to github.repo_roots.
    from planet_maiko.orchestration import resolve_repo_path, maybe_spawn

    repo_path = resolve_repo_path(scope_repo) if scope_repo else None
    if not repo_path:
        logger.warning(
            f"[auto-investigate] No local clone for {scope_repo!r} "
            f"(incident {incident_pupdate.id}); skipping. Add the repo "
            f"to Settings → Integrations → GitHub → Repository roots."
        )
        return None

    profile = maybe_spawn("investigation", scope_repo)

    task_id = f"task-incident-{uuid.uuid4().hex[:10]}"
    task = Task(
        id=task_id,
        title=f"Investigate: {incident_pupdate.title}",
        type="investigation",
        priority=incident_pupdate.priority or "high",
        status="new",
        source_pupdate_id=incident_pupdate.id,
        assigned_agent_id=profile.id,
        extra={
            "auto_spawned": True,
            "incident_id": incident_pupdate.id,
            "services": services,
            "repo": scope_repo,
            "pattern": (incident_pupdate.extra or {}).get("pattern") or [],
        },
    )
    db.session.add(task)
    db.session.commit()

    if auto_cfg.get("dry_run", True):
        logger.info(
            f"[auto-investigate] DRY RUN — created task {task.id} for incident "
            f"{incident_pupdate.id}. Flip brain.auto_investigate.dry_run=false to "
            f"spawn the agent automatically."
        )
        return task

    # Live kickoff. If anything below fails, the task stays as "new"
    # with assigned_agent_id set — the user can retry manually from the
    # Tasks UI, same as any other investigation task.
    from planet_maiko.agents.coding_agent import prepare, _kickoff_agent_headless

    try:
        full_prompt = _build_investigation_prompt(task, incident_pupdate)
        result = prepare(
            task_id=task.id,
            task_title=task.title,
            prompt=full_prompt,
            repo_path=repo_path,
            branch_prefix="maiko",
            auto_kickoff=False,
            use_worktree=True,
            agent_profile_id=profile.id,
            role="investigation",
        )
        if not result:
            logger.error(f"[auto-investigate] prepare() returned empty for {task.id}")
            return task

        working_path = result.get("working_path")
        _kickoff_agent_headless(
            profile.id, working_path, task.id,
            branch_name=None, plan_first=False, role="investigation",
        )

        task.status = "in_progress"
        extra = dict(task.extra or {})
        if working_path:
            extra["working_path"] = working_path
        if result.get("branch"):
            extra["branch"] = result["branch"]
        task.extra = extra
        db.session.commit()

        logger.info(
            f"[auto-investigate] Spawned {profile.display_name} on incident "
            f"{incident_pupdate.id}: task={task.id} ({count_today + 1}/{budget} today)"
        )
    except Exception as e:
        logger.exception(f"[auto-investigate] Kickoff failed for incident {incident_pupdate.id}: {e}")

    return task
