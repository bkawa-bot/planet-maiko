from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class ContextSelection(db.Model):
    """Tracks which learnings were included in an agent's brief for a task,
    and the outcome. This data powers the context optimization loop:

        1. Select learnings for brief → agent works → record outcome
        2. Over time, learn which learnings correlate with good outcomes
        3. Prefer high-success learnings, deprioritize low-success ones

    No ML needed - just counting success rates per learning.
    """
    __tablename__ = "context_selections"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.String(128), nullable=False, index=True)
    agent_profile_id = db.Column(db.String(128), db.ForeignKey("agent_profiles.id"), nullable=True)
    repo = db.Column(db.String(256), nullable=True)

    # Which learnings were included in the brief
    learning_ids = db.Column(db.JSON, default=list)  # [1, 3, 7, 12]

    # Outcome (recorded after task completes)
    outcome = db.Column(db.String(20), nullable=True)  # success, changes_requested, failed, cancelled
    outcome_recorded_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "agent_profile_id": self.agent_profile_id,
            "repo": self.repo,
            "learning_ids": self.learning_ids,
            "learning_count": len(self.learning_ids or []),
            "outcome": self.outcome,
            "outcome_recorded_at": iso_utc(self.outcome_recorded_at),
            "created_at": iso_utc(self.created_at),
        }
