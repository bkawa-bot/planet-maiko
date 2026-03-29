from datetime import datetime, timezone
from planet_maiko.database import db


class Tournament(db.Model):
    """A tournament evaluates which rule combinations work best for a task.

    Uses already-merged PRs as ground truth. Multiple strategies compete
    with different rule subsets, and an LLM judge scores the outputs.
    Results feed back into compile_brief() to improve rule selection.
    """
    __tablename__ = "tournaments"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    pr_repo = db.Column(db.String(256), nullable=False)
    pr_number = db.Column(db.Integer, nullable=False)
    pr_title = db.Column(db.String(512))
    pr_diff_summary = db.Column(db.Text)
    task_description = db.Column(db.Text)

    status = db.Column(db.String(20), default="pending")  # pending, running, completed, failed
    winning_strategy = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)

    entries = db.relationship("TournamentEntry", backref="tournament", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "pr_repo": self.pr_repo,
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
            "pr_diff_summary": self.pr_diff_summary,
            "task_description": self.task_description,
            "status": self.status,
            "winning_strategy": self.winning_strategy,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "entries": [e.to_dict() for e in self.entries] if self.entries else [],
        }


class TournamentEntry(db.Model):
    """A single strategy's attempt in a tournament."""
    __tablename__ = "tournament_entries"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournaments.id"), nullable=False)

    strategy = db.Column(db.String(50), nullable=False)  # relevant_5, random_5, all, agent_best
    learning_ids = db.Column(db.JSON, default=list)
    agent_profile_id = db.Column(db.String(128), nullable=True)

    output = db.Column(db.Text)
    score = db.Column(db.Float, nullable=True)  # 0-10 from LLM judge
    judge_reasoning = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "tournament_id": self.tournament_id,
            "strategy": self.strategy,
            "learning_ids": self.learning_ids,
            "agent_profile_id": self.agent_profile_id,
            "score": self.score,
            "judge_reasoning": self.judge_reasoning,
            "output_length": len(self.output) if self.output else 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
