from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


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

    # First-seen code context. Kept for backward compat with readers
    # that expect a single "primary" example on the row. The complete
    # list of occurrences (including this one) lives in `examples`.
    code_context = db.Column(db.Text, nullable=True)

    # Every place this signal's text showed up, with its own diff hunk.
    # Same comment on 3 files = 1 signal with 3 examples = 3 training
    # pairs. Shape:
    #   [{"path": str|None, "diff_hunk": str|None,
    #     "author": str, "line": int|None}, ...]
    examples = db.Column(db.JSON, default=list)

    learning_id = db.Column(db.Integer, db.ForeignKey("learnings.id"), nullable=True)
    aggregated = db.Column(db.Boolean, default=False)  # Has this been processed?
    incorporated_at = db.Column(db.DateTime, nullable=True)  # When included in a training dataset
    # True once the signal has been through LLM synthesis (or was
    # emitted by a source that sets a real category directly, like
    # CLI feedback). False means "category is the bootstrap default
    # and may be wrong" — cluster should wait for synthesis first.
    synthesized = db.Column(db.Boolean, default=False, index=True)

    # When this signal was created from a specific agent reply (feedback
    # message_type), points at that AgentMessage.id. Lets the Pack
    # Insights ritual undo a signal cleanly when the user drops the
    # agent's contribution during review — no fuzzy text+time matching.
    source_message_id = db.Column(db.Integer, nullable=True, index=True)

    # Stable id from the source system (GitHub comment id for PR-comment
    # signals, agent message id for agent feedback, etc). Used for
    # dedup on re-scrape — signal.text gets mutated by synthesis, so
    # text-based dedup silently fails once a signal has been synthesized.
    external_id = db.Column(db.String(64), nullable=True, index=True)

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
            "examples": self.examples or [],
            "learning_id": self.learning_id,
            "aggregated": self.aggregated,
            "synthesized": bool(self.synthesized),
            "external_id": self.external_id,
            "incorporated_at": iso_utc(self.incorporated_at),
            "created_at": iso_utc(self.created_at),
        }
