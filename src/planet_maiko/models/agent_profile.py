from datetime import datetime, timezone
from planet_maiko.database import db


class AgentProfile(db.Model):
    """Persistent agent identity with stats and specialization.

    Agents are characters in your town - they have names, avatars,
    personalities, and grow through experience. The profile persists
    across sessions and tracks their history.
    """
    __tablename__ = "agent_profiles"

    id = db.Column(db.String(128), primary_key=True)
    display_name = db.Column(db.String(100), nullable=False)
    avatar = db.Column(db.String(50), default="shiba")  # shiba, corgi, husky, poodle, golden, etc.
    breed = db.Column(db.String(50), default="pup")  # pup, junior, senior, expert
    flavor_text = db.Column(db.String(256), nullable=True)  # "Loves debugging. Afraid of CSS."

    # Stats
    tasks_completed = db.Column(db.Integer, default=0)
    tasks_failed = db.Column(db.Integer, default=0)
    prs_merged = db.Column(db.Integer, default=0)
    prs_changes_requested = db.Column(db.Integer, default=0)
    learnings_contributed = db.Column(db.Integer, default=0)

    # Specialization scores (JSON: {"api-service": 0.85, "search-service": 0.3})
    specializations = db.Column(db.JSON, default=dict)

    # The agent's proven set of learning IDs — built via training
    context_set = db.Column(db.JSON, default=list)

    # Lens: per-agent overrides (legacy, mostly unused)
    lens = db.Column(db.JSON, default=dict)

    # Flexible metadata (adapter_path, trained_on_examples, etc.)
    extra = db.Column(db.JSON, default=dict)

    archived = db.Column(db.Boolean, default=False)
    archived_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_active_at = db.Column(db.DateTime, nullable=True)

    def rank(self):
        """Compute rank from experience."""
        total = self.tasks_completed + self.tasks_failed
        if total >= 20 and self.success_rate() >= 0.8:
            return "expert"
        if total >= 10:
            return "senior"
        if total >= 3:
            return "junior"
        return "pup"

    def success_rate(self):
        total = self.tasks_completed + self.tasks_failed
        if total == 0:
            return 0.0
        return self.tasks_completed / total

    def to_dict(self):
        return {
            "id": self.id,
            "display_name": self.display_name,
            "avatar": self.avatar,
            "breed": self.breed,
            "rank": self.rank(),
            "flavor_text": self.flavor_text,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "prs_merged": self.prs_merged,
            "prs_changes_requested": self.prs_changes_requested,
            "learnings_contributed": self.learnings_contributed,
            "success_rate": round(self.success_rate(), 2),
            "specializations": self.specializations,
            "context_set": self.context_set or [],
            "extra": self.extra or {},
            "archived": self.archived or False,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_active_at": self.last_active_at.isoformat() if self.last_active_at else None,
        }
