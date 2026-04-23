"""Memo model — the canonical surface for persistent, user-facing items.

A Memo is anything that sticks around until the user has looked at it
or acted on it. Distinct from Pupdate, which is a transient queue
event (pollers fire them → automations route them → done). Distinct
from Task, which is concrete work with a worktree / agent / due date.

Memos live in the middle ground: the user needs to see or decide
about something, but there's no Task to spawn and the raw event
that caused it has already been routed.

Canonical kinds (open-set — plugins can register their own):
    - skill_result: a skill run produced user-facing output
    - notification: the notify_me automation action fired
    - agent_ready: agent finished a one-shot run, output waiting
    - agent_stuck: agent hit a blocker, needs a nudge
    - agent_proposal: agent proposed a pattern / task / follow-up
    - agent_plan: coding agent wrote a plan, wants approval
    - job_approval: an ask-first automation wants to run an AgentJob

Category drives UI rendering:
    - info: FYI, just needs to be read + dismissed
    - waiting: has a clear CTA the user needs to take
    - offer: a proposal the user can approve, edit, or dismiss

Status is the lifecycle. Only pending + seen surface to the overview
LLM's "what's on your plate" context; actioned + dismissed are archive.
"""

from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


VALID_STATUSES = ("pending", "seen", "actioned", "dismissed")
VALID_CATEGORIES = ("info", "waiting", "offer")


class Memo(db.Model):
    __tablename__ = "memos"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Free-form kind string so plugins can introduce their own without
    # a migration. The registered set lives in brain/memos.py's
    # create_memo helper for code-side documentation; the DB doesn't
    # enforce, which mirrors how Pupdate.type works.
    kind = db.Column(db.String(64), nullable=False, index=True)
    category = db.Column(db.String(16), nullable=False, default="info", index=True)

    title = db.Column(db.String(300), nullable=False)
    # Markdown body. Optional — a notification might just have a title.
    body = db.Column(db.Text, nullable=True)
    # Deep-link URL. For skill_results, can be empty (output is in body
    # or extra); for notifications, can be set to the triggering
    # pupdate's url so clicking the memo jumps there.
    url = db.Column(db.String(1024), nullable=True)

    # CTA fields for "waiting" category. cta_action is a short token
    # the UI maps to a handler — e.g. "approve" for a job_approval,
    # "review" for an agent_ready, "open" for a generic deep-link.
    cta_label = db.Column(db.String(64), nullable=True)
    cta_action = db.Column(db.String(64), nullable=True)

    priority = db.Column(db.String(20), default="normal", nullable=False, index=True)

    # Provenance. All optional.
    source_agent_id = db.Column(db.String(128), nullable=True, index=True)
    source_task_id = db.Column(db.String(128), nullable=True, index=True)
    source_pupdate_id = db.Column(db.String(128), nullable=True, index=True)

    # pending → seen (rendered at least once) → actioned (user took CTA)
    # OR dismissed (user said no / not now). Only pending + seen
    # surface to the overview LLM; actioned + dismissed are archive.
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)

    # Kind-specific payload. For job_approval memos: the job spec
    # (kind, title, description, scope_repo, priority) that
    # /memos/<id>/approve reads to mint the real AgentJob. For
    # skill_result memos: the full output text. For proposals:
    # the {draft} object the ProposalCard edits.
    extra = db.Column(db.JSON, default=dict)

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    seen_at = db.Column(db.DateTime, nullable=True)
    actioned_at = db.Column(db.DateTime, nullable=True)
    dismissed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "category": self.category,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "cta_label": self.cta_label,
            "cta_action": self.cta_action,
            "priority": self.priority,
            "source_agent_id": self.source_agent_id,
            "source_task_id": self.source_task_id,
            "source_pupdate_id": self.source_pupdate_id,
            "status": self.status,
            "extra": self.extra or {},
            "created_at": iso_utc(self.created_at),
            "seen_at": iso_utc(self.seen_at),
            "actioned_at": iso_utc(self.actioned_at),
            "dismissed_at": iso_utc(self.dismissed_at),
        }
