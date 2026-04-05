from datetime import datetime, timezone
from planet_maiko.database import db


class SkillResult(db.Model):
    """Persistent output from a skill run.

    Unlike pupdates (ephemeral notifications), skill results are
    lasting artifacts that Maiko created for you - morning briefs,
    brainstorms, investigations, repo analyses.
    """
    __tablename__ = "skill_results"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    skill_name = db.Column(db.String(50), nullable=False, index=True)
    title = db.Column(db.String(256), nullable=False)
    content = db.Column(db.Text, nullable=False)
    context_summary = db.Column(db.Text, nullable=True)  # what data was fed in
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "skill_name": self.skill_name,
            "title": self.title,
            "content": self.content,
            "context_summary": self.context_summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
