from datetime import datetime, timezone
from sqlalchemy import event
from planet_maiko.database import db, iso_utc


# Pupdate types that block on the user vs. everything else
# (running work, FYIs, ambient signals). Keep this list in sync with
# the Home "Also Waiting" WAITING_TYPES — the inbox Action strip and
# the home surface pull from the same decision.
ACTION_TYPES = frozenset({
    "agent_plan_for_approval",
    "agent_ready_for_review",
    "agent_stuck",
    "agent_proposal",
    "pr_review_requested",
    "pr_changes_requested",
    "incident",
    "missing_local_clone",
    "stuck_task",
    "conflict",
    "deploy_rollback",
})


def categorize(type_):
    """Map a pupdate type to 'action' (the user needs to do something)
    or 'activity' (FYI / the pack is chattering). Returns 'activity'
    for unknown types — noisy defaults are better than silent misses,
    and explicit types should be added to ACTION_TYPES when they need
    to surface.
    """
    return "action" if type_ in ACTION_TYPES else "activity"


class Pupdate(db.Model):
    __tablename__ = "pupdates"

    id = db.Column(db.String(64), primary_key=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    source = db.Column(db.String(50), nullable=False)  # e.g. "github", "linear", "calendar"
    source_id = db.Column(db.String(256), nullable=True)  # dedup key from the source
    type = db.Column(db.String(100), nullable=False)  # e.g. "pr_review_requested", "linear_assigned"
    priority = db.Column(db.String(20), default="normal", index=True)  # low, normal, high, urgent
    # Whether this pupdate blocks on the user. Drives the inbox Action
    # strip and Home "Also Waiting". Computed from `type` at insert time.
    category = db.Column(db.String(16), default="activity", index=True)
    title = db.Column(db.String(512), nullable=False)
    body = db.Column(db.Text, nullable=True)
    url = db.Column(db.String(1024), nullable=True)
    actionable = db.Column(db.Boolean, default=False)
    action_hint = db.Column(db.String(256), nullable=True)  # e.g. "Review PR", "Create task"
    tags = db.Column(db.JSON, default=list)
    dismissed = db.Column(db.Boolean, default=False)
    dismissed_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    extra = db.Column("metadata", db.JSON, default=dict)
    brain_processed = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": iso_utc(self.timestamp),
            "source": self.source,
            "source_id": self.source_id,
            "type": self.type,
            "priority": self.priority,
            "category": self.category or "activity",
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "actionable": self.actionable,
            "action_hint": self.action_hint,
            "tags": self.tags,
            "dismissed": self.dismissed,
            "dismissed_at": iso_utc(self.dismissed_at),
            "expires_at": iso_utc(self.expires_at),
            "metadata": self.extra,
            "brain_processed": self.brain_processed,
        }


@event.listens_for(Pupdate, "before_insert")
def _set_category(mapper, connection, target):
    # Respect an explicit category if a caller already set one
    # (rare but possible for special cases); otherwise derive from type.
    if not target.category or target.category == "activity":
        target.category = categorize(target.type)
