from datetime import datetime, timezone

from planet_maiko.database import db, iso_utc


class TokenUsage(db.Model):
    """One row per LLM call Maiko makes through the runtime.

    Covers Maiko's INTERNAL calls (home overview, maiko-chat, pack
    router, learning synthesis, etc.). Does NOT cover the headless
    claude sessions agents run in their worktrees — those are billed
    against the user's interactive Claude Code session, not Agent SDK
    credits, and their usage lives in Anthropic's own logs.

    The point of this table is the runaway-burn audit: a buggy loop
    in our own code (re-clustering a rule pool, regenerating an
    overview every cycle, etc.) is what would silently rack up cost.
    Sum input_tokens + output_tokens over a day + source and the
    pattern jumps out.
    """

    __tablename__ = "token_usage"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    timestamp = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    # What inside Maiko triggered this call. Free-form so new callsites
    # can adopt a stable string without a migration. "unknown" when the
    # caller didn't pass one.
    source = db.Column(db.String(100), nullable=False, default="unknown", index=True)
    model = db.Column(db.String(100), nullable=True)
    input_tokens = db.Column(db.Integer, default=0, nullable=False)
    output_tokens = db.Column(db.Integer, default=0, nullable=False)
    # Cache fields — the prompt-cache discount the runtime gets when
    # the system / preamble blob is reused. Tracking separately so a
    # cache-miss spike is visible.
    cache_creation_tokens = db.Column(db.Integer, default=0, nullable=False)
    cache_read_tokens = db.Column(db.Integer, default=0, nullable=False)
    total_cost_usd = db.Column(db.Float, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    session_id = db.Column(db.String(128), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": iso_utc(self.timestamp),
            "source": self.source,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_cost_usd": self.total_cost_usd,
            "duration_ms": self.duration_ms,
            "session_id": self.session_id,
        }
