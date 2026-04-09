from datetime import datetime, timezone
from planet_maiko.database import db


class Learning(db.Model):
    """A graduated rule learned from accumulated signals.

    Learnings start as "pending" and graduate to "active" once enough
    signals confirm them. Different categories have different thresholds.

    Active learnings become part of the coding guidelines that agents
    use when working on code.
    """
    __tablename__ = "learnings"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    rule = db.Column(db.Text, nullable=False)  # The learned rule in plain English
    category = db.Column(db.String(50), nullable=False, index=True)

    # Scope: which repos/languages this applies to (null = global)
    scope_repo = db.Column(db.String(256), nullable=True, index=True)
    scope_language = db.Column(db.String(50), nullable=True)

    confidence = db.Column(db.Float, default=0.0)  # 0.0 to 1.0
    signal_count = db.Column(db.Integer, default=0)

    source = db.Column(db.String(20), default="auto")  # auto, manual, promoted
    status = db.Column(db.String(20), default="pending", index=True)
    # Status: pending, active, dismissed

    # Aggregation key for dedup (category:repo:language:normalized_text_prefix)
    aggregation_key = db.Column(db.String(512), nullable=True, index=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    last_signal_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "rule": self.rule,
            "category": self.category,
            "scope_repo": self.scope_repo,
            "scope_language": self.scope_language,
            "confidence": self.confidence,
            "signal_count": self.signal_count,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_signal_at": self.last_signal_at.isoformat() if self.last_signal_at else None,
        }
