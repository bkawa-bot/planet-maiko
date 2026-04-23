"""Memo creation + status transition helpers.

Every producer (skill runs, notify_me, agent reply handler, ask-first
automations) routes through create_memo() so we have one place that
knows the field shape. Status transitions go through mark_seen /
mark_actioned / mark_dismissed so the timestamps stay consistent.

Kept deliberately thin — the Memo model holds the schema, this file
holds the ergonomics.
"""

import logging
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.memo import Memo, VALID_CATEGORIES, VALID_STATUSES

logger = logging.getLogger(__name__)


# Canonical kinds documented here so callers have a reference and we
# can grep for every producer. Not DB-enforced — plugins can register
# their own — but emit a debug log when an unknown kind lands so we
# notice typos early.
CANONICAL_KINDS = {
    "skill_result",
    "notification",
    "agent_ready",
    "agent_stuck",
    "agent_proposal",
    "agent_plan",
    "job_approval",
}


def create_memo(
    *,
    kind,
    category,
    title,
    body=None,
    url=None,
    cta_label=None,
    cta_action=None,
    priority="normal",
    source_agent_id=None,
    source_task_id=None,
    source_pupdate_id=None,
    extra=None,
):
    """Create a Memo row. Caller is responsible for db.session.commit().

    Validates category against the fixed set; kind is open but logged
    if not in CANONICAL_KINDS so typos surface. Returns the new Memo
    (already added to the session — the caller's commit picks it up
    with their other writes).
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"memo category must be one of {VALID_CATEGORIES}, got {category!r}"
        )
    if kind not in CANONICAL_KINDS:
        logger.debug(
            f"[memo] Unknown kind {kind!r} — fine for plugins, but check for typos"
        )

    memo = Memo(
        kind=kind,
        category=category,
        title=(title or "")[:300],
        body=body,
        url=url,
        cta_label=cta_label,
        cta_action=cta_action,
        priority=priority or "normal",
        source_agent_id=source_agent_id,
        source_task_id=source_task_id,
        source_pupdate_id=source_pupdate_id,
        extra=extra or {},
    )
    db.session.add(memo)
    return memo


def mark_seen(memo):
    """Flip pending → seen. No-op for other statuses (once actioned or
    dismissed, `seen` is moot)."""
    if memo.status == "pending":
        memo.status = "seen"
        memo.seen_at = datetime.now(timezone.utc)


def mark_actioned(memo):
    """User took the CTA. Terminal state."""
    memo.status = "actioned"
    memo.actioned_at = datetime.now(timezone.utc)


def mark_dismissed(memo):
    """User said no / not now. Terminal state."""
    memo.status = "dismissed"
    memo.dismissed_at = datetime.now(timezone.utc)


# Approve-handler registry. Kinds that have an actionable CTA register
# a callable here: fn(memo) -> {success: bool, ...} that performs the
# kind-specific work (e.g. job_approval mints the AgentJob row). The
# memos_api.approve endpoint looks up by kind; unregistered kinds get
# a 400 so the UI knows the memo isn't actionable via approve.
APPROVE_HANDLERS = {}


def register_approve_handler(kind, handler):
    """Wire a kind to its approve-time side effect.

    Producers call this at import time so the handler is available
    when /memos/<id>/approve dispatches. Keeps approve behavior
    colocated with the producer that knows what `extra` means.
    """
    APPROVE_HANDLERS[kind] = handler
