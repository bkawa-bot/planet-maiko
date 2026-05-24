"""Rule decay check: find active Learnings that haven't seen a new
signal or a user confirmation in a long time, and offer the user a
batched cleanup memo.

Stale rule = active rule whose most recent timestamp (the latest of
last_confirmed_at, last_signal_at, created_at) is older than the
configured cutoff. Default cutoff: 90 days.

Memo cadence: once per cooldown_days (default 7). If a rules_decay
memo already exists within that window, this run is a no-op. The
user clicking "Keep all" bumps last_confirmed_at on every listed
rule so we don't re-ask. Dismissing the memo also rides the cooldown
so the user gets a quiet week before being asked again. Individual
archiving stays a manual action on the Knowledge page.
"""

from datetime import datetime, timedelta, timezone
import logging

from planet_maiko.database import db

logger = logging.getLogger(__name__)


def _to_naive_utc(dt):
    """SQLite stores DateTime columns as naive UTC. Anything we compare
    against rows needs to be naive too."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def find_stale_rules(stale_days: int = 90):
    """Active Learnings whose most-recent signal/confirm/creation is
    older than the cutoff. Returns the model rows directly."""
    from planet_maiko.models.learning import Learning

    cutoff = _to_naive_utc(
        datetime.now(timezone.utc) - timedelta(days=stale_days)
    )
    rows = Learning.query.filter(Learning.status == "active").all()
    stale = []
    for r in rows:
        anchor = max(
            [t for t in (r.last_signal_at, r.last_confirmed_at, r.created_at) if t is not None],
            default=None,
        )
        if anchor is not None and anchor < cutoff:
            stale.append(r)
    return stale


def maybe_check_rules_decay(stale_days: int = 90, cooldown_days: int = 7) -> dict:
    """Drop a rules_decay memo for stale Learnings if we haven't asked
    in the cooldown window. Idempotent. Cheap when nothing is stale
    or when the cooldown is active.
    """
    from planet_maiko.models.memo import Memo
    from planet_maiko.brain.memos import create_memo

    # Cooldown gate: skip if any rules_decay memo (pending, seen, or
    # actioned) was created in the cooldown window.
    cooldown_cutoff = _to_naive_utc(
        datetime.now(timezone.utc) - timedelta(days=cooldown_days)
    )
    recent = (
        Memo.query
        .filter(Memo.kind == "rules_decay")
        .filter(Memo.created_at >= cooldown_cutoff)
        .first()
    )
    if recent is not None:
        return {"skipped": "cooldown", "memo_id": recent.id}

    stale = find_stale_rules(stale_days=stale_days)
    if not stale:
        return {"stale_count": 0}

    rule_ids = [r.id for r in stale]
    body_lines = [
        f"{len(stale)} active rule{'s' if len(stale) != 1 else ''} "
        f"haven't gotten a new signal in {stale_days}+ days.",
        "",
        "Approve to mark them as still relevant (won't ask again "
        f"for {cooldown_days} days). Archive individually on the "
        "Knowledge page if any are obsolete.",
        "",
    ]
    for r in stale[:15]:
        snippet = (r.rule or "").splitlines()[0][:100]
        body_lines.append(f"  - [{r.category}] {snippet}")
    if len(stale) > 15:
        body_lines.append(f"  ... and {len(stale) - 15} more.")
    body = "\n".join(body_lines)

    memo = create_memo(
        kind="rules_decay",
        category="offer",
        title=(
            f"Stale rules check: {len(stale)} candidate"
            f"{'s' if len(stale) != 1 else ''}"
        ),
        body=body,
        priority="low",
        cta_label="Keep all",
        cta_action="approve",
        extra={"rule_ids": rule_ids, "stale_days": stale_days},
    )
    db.session.commit()
    logger.info(
        f"[rules-decay] dropped memo #{memo.id} with {len(stale)} stale rule(s)"
    )
    return {"stale_count": len(stale), "memo_id": memo.id}
