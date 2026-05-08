"""Memo approve-time handlers.

Registers the kind-specific side effects the /memos/<id>/approve
endpoint runs before marking a memo actioned. Colocated here (rather
than in brain/memos.py) so the model + API layer stays small and
import-safe; this module is the one that imports Task / Automation /
etc. at registration time.

Imported from app.py during blueprint setup so registration happens
once per app boot.
"""

import logging
import os
import uuid

from planet_maiko.database import db
from planet_maiko.brain.memos import register_approve_handler

logger = logging.getLogger(__name__)


class MemoApproveNeedsInput(Exception):
    """Raised by an approve handler when it can't proceed without
    additional input from the user. The endpoint converts this to a
    422 response carrying the `payload` so the frontend can render
    the right prompt + retry the call.
    """
    def __init__(self, kind, payload):
        super().__init__(kind)
        self.kind = kind   # short slug e.g. "needs_repo"
        self.payload = payload


def _approve_agent_proposal(memo, data=None):
    """Approve an agent-emitted TASK/PROPOSAL block.

    The draft lives in memo.extra.draft — it's already been through
    the parser (agent_output.py). We mint a routed Task and let the
    cycle's route() / is_ready() pick the assignee. The memo itself
    transitions to "actioned" in the /memos approve endpoint after
    this returns.

    Returns dict with the created task for the client.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import route, is_ready

    extra = memo.extra or {}
    draft = extra.get("draft") or {}
    if not draft.get("title"):
        raise ValueError("memo.extra.draft.title is required to mint a task")

    task = Task(
        id=f"task-{uuid.uuid4().hex[:10]}",
        title=draft["title"],
        type=draft.get("type") or "todo",
        priority=draft.get("priority") or memo.priority or "normal",
        status="new",
        url=memo.url,
        extra={
            "description": draft.get("description") or memo.body or "",
            "repo": draft.get("repo") or "",
            "category": draft.get("category") or "",
            "from_proposal_memo": memo.id,
        },
        tags=["from_proposal"],
        depends_on=draft.get("depends_on") or [],
    )
    db.session.add(task)
    db.session.flush()

    override = draft.get("assigned_agent_id")
    if override:
        task.assigned_agent_id = override
    else:
        route(task)
    task.status = "blocked" if not is_ready(task) else "new"

    logger.info(
        f"[memo-approve] agent_proposal memo #{memo.id} → task {task.id}"
    )
    return {"task": task.to_dict()}


def _approve_job_approval(memo, data=None):
    """Approve a pending ask-first AgentJob.

    The job spec lives in memo.extra.job_spec — built by
    _act_run_agent_job / _act_spawn_agent_job_from_pupdate when
    ask_first=True. Approving mints the real AgentJob with status=
    queued so the next cycle's execute phase picks it up.

    If the spec scopes to a repo that has no local clone (and the
    request body didn't supply a `repo_path` override), raise
    MemoApproveNeedsInput so the endpoint can prompt the user to
    pick a repo before the job gets minted. Without this pre-flight,
    the job lands queued, the next cycle's execute phase fails on
    resolve_repo_path() returning None, and the user has no way to
    retry with a different path — they're stuck dismissing the failed
    job and re-triggering the upstream automation.
    """
    from datetime import datetime, timezone
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.config import load_config
    from planet_maiko.orchestration import resolve_repo_path

    extra = memo.extra or {}
    spec = extra.get("job_spec") or {}
    if not spec.get("kind") or not spec.get("title"):
        raise ValueError("memo.extra.job_spec is missing kind/title")

    data = data or {}
    repo_path_override = (data.get("repo_path") or "").strip() or None
    scope_repo = spec.get("scope_repo")

    # Pre-flight repo lookup. Skip when:
    #   - the user already supplied an explicit repo_path
    #   - the spec doesn't scope to a repo at all (some skill jobs
    #     don't need a worktree — those route through repo-less)
    if not repo_path_override and scope_repo:
        local = resolve_repo_path(scope_repo)
        if not local:
            cfg_github = (load_config().get("github") or {})
            configured = cfg_github.get("repos") or []
            roots = cfg_github.get("repo_roots") or []
            # Resolve every configured repo to a local clone so the
            # picker only offers repos the user actually has on disk.
            choices = []
            for r in configured:
                p = resolve_repo_path(r)
                if p:
                    choices.append({"repo": r, "local_path": p})
            raise MemoApproveNeedsInput("needs_repo", {
                "scope_repo": scope_repo,
                "kind": spec.get("kind"),
                "title": spec.get("title"),
                "configured_repos": choices,
                "repo_roots": roots,
                "memo_id": memo.id,
            })

    # Carry the triggering-pupdate pointers forward so the execute
    # phase can compose a rich TASK.md from them.
    job_extra = {
        "from_automation": spec.get("automation_id"),
        "approved_via_memo": memo.id,
    }
    if extra.get("triggered_by_pupdate"):
        job_extra["triggered_by_pupdate"] = extra["triggered_by_pupdate"]
    if extra.get("triggered_by_pupdates"):
        job_extra["triggered_by_pupdates"] = extra["triggered_by_pupdates"]
    if extra.get("pupdate_snapshot"):
        job_extra["pupdate_snapshot"] = extra["pupdate_snapshot"]
    if extra.get("specialty_id"):
        job_extra["specialty_id"] = extra["specialty_id"]
    # User-picked repo path overrides resolve_repo_path() in
    # execute_jobs. Persisted on the job so the next-cycle pickup
    # uses it directly without re-resolving.
    if repo_path_override:
        job_extra["repo_path_override"] = repo_path_override

    job_id = f"job-{uuid.uuid4().hex[:10]}"
    job = AgentJob(
        id=job_id,
        kind=spec["kind"],
        title=spec["title"],
        description=spec.get("description") or "",
        scope_repo=scope_repo,
        priority=spec.get("priority") or "normal",
        created_by="automation",
        automation_id=spec.get("automation_id"),
        requires_approval=False,
        status="queued",
        approved_by="user",
        approved_at=datetime.now(timezone.utc),
        extra=job_extra,
    )
    db.session.add(job)
    db.session.flush()
    logger.info(
        f"[memo-approve] job_approval memo #{memo.id} → "
        f"AgentJob {job.id} (kind={spec['kind']}"
        f"{', repo_path_override set' if repo_path_override else ''})"
    )
    return {"job_id": job.id, "kind": spec["kind"]}


def register_all():
    """Wire every approve handler. Idempotent — safe to call on each
    app boot."""
    register_approve_handler("agent_proposal", _approve_agent_proposal)
    register_approve_handler("job_approval", _approve_job_approval)
