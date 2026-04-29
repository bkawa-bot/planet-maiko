from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class Learning(db.Model):
    """A rule learned from accumulated signals.

    Every Learning starts as "pending" and only becomes "active" when
    the user explicitly approves it — there's no auto-graduation.
    signal_count is just metadata (how many confirming signals back
    this rule) so the UI can sort or gate approval on evidence; it's
    not a graduation gate.
    """
    __tablename__ = "learnings"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    rule = db.Column(db.Text, nullable=False)  # The learned rule in plain English
    category = db.Column(db.String(50), nullable=False, index=True)

    # Origin repo (where the first signal came from). Stays fixed for
    # provenance. When the same rule shows up across >= GLOBAL_PROMOTE
    # distinct repos, we flip is_global so training pipes this learning
    # into every repo's LoRA dataset.
    scope_repo = db.Column(db.String(256), nullable=True, index=True)
    scope_language = db.Column(db.String(50), nullable=True)
    is_global = db.Column(db.Boolean, default=False, index=True)

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

    # RAG retrieval fields. violation_description is a Claude-generated
    # paragraph describing what code that VIOLATES this rule looks like
    # in this team's actual codebase, grounded in historical signals.
    # violation_embedding is the embedding of that description, used for
    # cosine similarity against new diffs at review time.
    # generated_at + signal_count_at_gen let us detect when to regenerate
    # (rule text changed, or enough new signals have joined the cluster
    # to warrant a refreshed description).
    violation_description = db.Column(db.Text, nullable=True)
    violation_embedding = db.Column(db.JSON, nullable=True)
    violation_description_generated_at = db.Column(db.DateTime, nullable=True)
    violation_description_signal_count = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "rule": self.rule,
            "category": self.category,
            "scope_repo": self.scope_repo,
            "scope_language": self.scope_language,
            "is_global": bool(self.is_global),
            "confidence": self.confidence,
            "signal_count": self.signal_count,
            "source": self.source,
            "status": self.status,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
            "last_signal_at": iso_utc(self.last_signal_at),
            "has_violation_description": bool(self.violation_description),
            "violation_description_generated_at": iso_utc(self.violation_description_generated_at),
        }
