from datetime import datetime, timezone
from planet_maiko.database import db


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.String(128), primary_key=True)
    title = db.Column(db.String(512), nullable=False)
    # type — coding: todo/bug/feature/review; orchestration roles:
    # investigation, repo_analysis. Used by the router to pick an agent role.
    type = db.Column(db.String(50), default="todo")
    # Statuses: blocked (has unfinished deps) → new (ready to start) →
    # in_progress → review/done/cancelled. "blocked" is new for the
    # orchestration layer.
    status = db.Column(db.String(50), default="new", index=True)
    priority = db.Column(db.String(20), default="normal", index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    source_pupdate_id = db.Column(db.String(64), db.ForeignKey("pupdates.id"), nullable=True)
    project_id = db.Column(db.String(128), db.ForeignKey("projects.id"), nullable=True)
    url = db.Column(db.String(1024), nullable=True)
    tags = db.Column(db.JSON, default=list)
    extra = db.Column("metadata", db.JSON, default=dict)
    assigned_agent_id = db.Column(db.String(128), nullable=True)
    due_date = db.Column(db.String(20), nullable=True)  # ISO date string YYYY-MM-DD
    # List of task IDs this task depends on. While any dep is not done, the
    # task stays in "blocked" status. Stored as JSON for SQLite simplicity.
    depends_on = db.Column(db.JSON, default=list)

    source_pupdate = db.relationship("Pupdate", backref="tasks")
    project = db.relationship("Project", backref="tasks")

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "status": self.status,
            "priority": self.priority,
            "due_date": self.due_date,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "source_pupdate_id": self.source_pupdate_id,
            "project_id": self.project_id,
            "assigned_agent_id": self.assigned_agent_id,
            "url": self.url,
            "tags": self.tags,
            "metadata": self.extra,
            "depends_on": self.depends_on or [],
        }
