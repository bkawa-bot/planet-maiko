"""Memo approve-time handlers.

Registers the kind-specific side effects the /memos/<id>/approve
endpoint runs before marking a memo actioned. Colocated here (rather
than in brain/memos.py) so the model + API layer stays small and
import-safe; this module is the one that imports Task / AgentGoal /
Automation / etc. at registration time.

Imported from app.py during blueprint setup so registration happens
once per app boot.
"""

import logging
import uuid

from planet_maiko.database import db
from planet_maiko.brain.memos import register_approve_handler

logger = logging.getLogger(__name__)


def _approve_agent_proposal(memo):
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


def register_all():
    """Wire every approve handler. Idempotent — safe to call on each
    app boot."""
    register_approve_handler("agent_proposal", _approve_agent_proposal)
