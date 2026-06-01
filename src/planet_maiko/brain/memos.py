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
    # A workflow paused at an approval gate; the memo carries the upstream
    # plan and approves / rejects the gate inline.
    "flow_approval",
    # A finished flow's coder branch, reviewed and waiting for the human to
    # look at the diff and open a PR.
    "flow_diff_ready",
    # An agent reply that explicitly addressed the user (recipient="user"
    # on the originating AgentMessage). Surfaces the message in MemosPane
    # so it doesn't get lost inside the chat thread.
    "agent_message",
}


def find_dedup_match(kind, source_pupdate_id):
    """Return an existing live memo with this (kind, source_pupdate_id),
    or None if none exists.

    "Live" = not dismissed. We allow seen and actioned matches because
    a duplicate-creating automation re-firing on the same pupdate
    shouldn't bury what the user already saw and dealt with — the right
    behavior is to surface the original, not stack a fresh copy.

    Only matches when source_pupdate_id is set; memos minted from agent
    output (no pupdate) opt out of pupdate-keyed dedup by simply not
    passing one. Callers that need a different dedup key (e.g. agent
    message dedup on source_pupdate_id alone is too coarse) handle it
    themselves.
    """
    if not source_pupdate_id:
        return None
    return (
        Memo.query
        .filter(Memo.kind == kind)
        .filter(Memo.source_pupdate_id == source_pupdate_id)
        .filter(Memo.status != "dismissed")
        .first()
    )


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
    dedup=True,
):
    """Create a Memo row, or return the existing duplicate.

    Caller is responsible for db.session.commit().

    Validates category against the fixed set; kind is open but logged
    if not in CANONICAL_KINDS so typos surface. Returns the new Memo
    (already added to the session — the caller's commit picks it up
    with their other writes).

    `dedup=True` (the default) plus a non-empty `source_pupdate_id`
    short-circuits to an existing live memo with the same (kind,
    source_pupdate_id), so re-firing automations on the same pupdate
    don't stack duplicates in the user's inbox. Pass `dedup=False` for
    cases where the same kind+pupdate genuinely should produce
    multiple memos.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"memo category must be one of {VALID_CATEGORIES}, got {category!r}"
        )
    if kind not in CANONICAL_KINDS:
        logger.debug(
            f"[memo] Unknown kind {kind!r} — fine for plugins, but check for typos"
        )

    if dedup:
        existing = find_dedup_match(kind, source_pupdate_id)
        if existing is not None:
            logger.debug(
                f"[memo] dedup hit: kind={kind} pupdate={source_pupdate_id} "
                f"-> reusing memo #{existing.id}"
            )
            return existing

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
