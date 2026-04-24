from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.String(128), primary_key=True)
    title = db.Column(db.String(512), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="planning", index=True)  # planning, approved, active, paused, done, cancelled
    priority = db.Column(db.String(20), default="normal", index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    source_type = db.Column(db.String(50), nullable=True)  # e.g. "linear", "github", "manual"
    source_id = db.Column(db.String(256), nullable=True)
    source_url = db.Column(db.String(1024), nullable=True)
    extra = db.Column("metadata", db.JSON, default=dict)
    phases = db.Column(db.JSON, default=list)
    # Each phase: {"number": 1, "title": "...", "status": "pending|active|done",
    #              "repo": "...", "description": "...", "depends_on": []}
    current_phase = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "metadata": self.extra,
            "phases": self.phases,
            "current_phase": self.current_phase,
        }
