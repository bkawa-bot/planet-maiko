from datetime import datetime, timezone
from planet_maiko.database import db


class Pupdate(db.Model):
    __tablename__ = "pupdates"

    id = db.Column(db.String(64), primary_key=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    source = db.Column(db.String(50), nullable=False)  # e.g. "github", "linear", "calendar"
    source_id = db.Column(db.String(256), nullable=True)  # dedup key from the source
    type = db.Column(db.String(100), nullable=False)  # e.g. "pr_review_requested", "linear_assigned"
    priority = db.Column(db.String(20), default="normal")  # low, normal, high, urgent
    title = db.Column(db.String(512), nullable=False)
    body = db.Column(db.Text, nullable=True)
    url = db.Column(db.String(1024), nullable=True)
    actionable = db.Column(db.Boolean, default=False)
    action_hint = db.Column(db.String(256), nullable=True)  # e.g. "Review PR", "Create task"
    tags = db.Column(db.JSON, default=list)
    read = db.Column(db.Boolean, default=False)
    dismissed = db.Column(db.Boolean, default=False)
    dismissed_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    extra = db.Column("metadata", db.JSON, default=dict)
    brain_processed = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "source": self.source,
            "source_id": self.source_id,
            "type": self.type,
            "priority": self.priority,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "actionable": self.actionable,
            "action_hint": self.action_hint,
            "tags": self.tags,
            "read": self.read,
            "dismissed": self.dismissed,
            "dismissed_at": self.dismissed_at.isoformat() if self.dismissed_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": self.extra,
            "brain_processed": self.brain_processed,
        }
