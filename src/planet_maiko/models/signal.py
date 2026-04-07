from datetime import datetime, timezone
from planet_maiko.database import db


class Signal(db.Model):
    """Raw feedback event that feeds into the learning system.

    Sources: PR review comments, user dismissal patterns, agent feedback,
    manual input. Signals accumulate and aggregate into Learnings.
    """
    __tablename__ = "signals"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    category = db.Column(db.String(50), nullable=False, index=True)
    # Categories: null_safety, error_handling, performance, testing, style,
    # naming, docs, api_design, architecture, security, domain_knowledge,
    # pattern, gotcha, team

    text = db.Column(db.Text, nullable=False)  # The actual feedback/observation
    source_type = db.Column(db.String(50), nullable=False)
    # Source types: pr_comment, user_action, agent_discovery, manual

    reviewer = db.Column(db.String(100), nullable=True)  # Who gave the feedback
    severity = db.Column(db.String(20), default="suggestion")
    # Severity: suggestion, warning, blocking

    repo = db.Column(db.String(256), nullable=True)
    language = db.Column(db.String(50), nullable=True)
    file_path = db.Column(db.String(512), nullable=True)

    code_context = db.Column(db.Text, nullable=True)  # The code that triggered this feedback

    learning_id = db.Column(db.Integer, db.ForeignKey("learnings.id"), nullable=True)
    aggregated = db.Column(db.Boolean, default=False)  # Has this been processed?
    incorporated_at = db.Column(db.DateTime, nullable=True)  # When included in a training dataset

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    learning = db.relationship("Learning", backref="signals")

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "text": self.text,
            "source_type": self.source_type,
            "reviewer": self.reviewer,
            "severity": self.severity,
            "repo": self.repo,
            "language": self.language,
            "file_path": self.file_path,
            "code_context": self.code_context,
            "learning_id": self.learning_id,
            "aggregated": self.aggregated,
            "incorporated_at": self.incorporated_at.isoformat() if self.incorporated_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
