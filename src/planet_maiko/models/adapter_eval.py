"""AdapterEval — persisted precision/recall/F1 for a LoRA adapter run.

Each call to `evaluate_adapter()` writes one row here so we can track
whether adapter quality is trending up or down across retrains. The
canonical key is `adapter_path`: the same adapter evaluated multiple
times (different holdout splits, more training data accumulated)
produces one row per run, all sharing the same path. `adapter_version`
is the directory basename so the UI can show "repo-20250423-120000"
without parsing the path.

Read paths:
    - latest_for_adapter(adapter_path): most recent eval row
    - history_for_adapter(adapter_path, limit): trend for a dashboard

Written from brain/learning/lora_eval.py at the end of evaluate_adapter().
"""

from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class AdapterEval(db.Model):
    __tablename__ = "adapter_evals"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    adapter_path = db.Column(db.String(1024), nullable=False, index=True)
    adapter_version = db.Column(db.String(256), nullable=True)
    repo = db.Column(db.String(256), nullable=True, index=True)

    precision = db.Column(db.Float, nullable=False, default=0.0)
    recall = db.Column(db.Float, nullable=False, default=0.0)
    f1 = db.Column(db.Float, nullable=False, default=0.0, index=True)

    tp = db.Column(db.Integer, nullable=False, default=0)
    fp = db.Column(db.Integer, nullable=False, default=0)
    fn = db.Column(db.Integer, nullable=False, default=0)
    tn = db.Column(db.Integer, nullable=False, default=0)
    test_count = db.Column(db.Integer, nullable=False, default=0)
    holdout_fraction = db.Column(db.Float, nullable=True)

    per_category = db.Column(db.JSON, default=dict)
    extra = db.Column(db.JSON, default=dict)

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "adapter_path": self.adapter_path,
            "adapter_version": self.adapter_version,
            "repo": self.repo,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "test_count": self.test_count,
            "holdout_fraction": self.holdout_fraction,
            "per_category": self.per_category or {},
            "extra": self.extra or {},
            "created_at": iso_utc(self.created_at),
        }

    @classmethod
    def latest_for_adapter(cls, adapter_path):
        return (
            cls.query
            .filter(cls.adapter_path == adapter_path)
            .order_by(cls.created_at.desc())
            .first()
        )

    @classmethod
    def history_for_adapter(cls, adapter_path, limit=20):
        return (
            cls.query
            .filter(cls.adapter_path == adapter_path)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )
