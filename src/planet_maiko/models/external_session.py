"""ExternalSession model — A2A awareness for sessions Maiko didn't spawn.

Phase A of the external-orchestrator MCP surface. Third-party tools
running LLM coding sessions outside Planet Maiko register their
session here so the awareness phase can include their worktree in the
pairwise conflict scan. Without this, the detector only sees
worktrees Maiko itself prepared under `.maiko-worktrees/`, and an
external agent editing the same file silently collides.

Lifecycle is minimal on purpose:
    - Orchestrator POSTs /sessions/register when it spins up a session.
    - Detector picks up every active row on each 5-minute cycle.
    - Orchestrator POSTs /sessions/<id>/complete when done, which flips
      status="completed" and fills completed_at. Completed rows stay
      for audit but drop out of the active scan.

No auth, no multi-tenant separation — Phase A is local-brain only.
"""

from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class ExternalSession(db.Model):
    __tablename__ = "external_sessions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Consumer-provided OR server-generated uuid4 hex. Unique + indexed
    # so /sessions/<session_id>/... lookups stay O(log n) when the
    # table gets chatty.
    session_id = db.Column(db.String(128), unique=True, nullable=False, index=True)

    # Optional audit label — the tool name that registered this session.
    # Free-form; we don't enforce an allowlist.
    consumer = db.Column(db.String(64), nullable=True)

    # Repo identifier in "org/name" form. Not a FK — external
    # orchestrators may work on repos Maiko has never seen.
    repo = db.Column(db.String(256), nullable=False)

    # Absolute path to the worktree. The awareness phase feeds this
    # straight into git diff, so a relative or bogus path will just
    # silently produce no conflicts for this session.
    worktree_path = db.Column(db.String(1024), nullable=False)

    # Short task description. Nullable — Phase A doesn't require it,
    # though future phases may surface it in conflict messages.
    hint = db.Column(db.Text, nullable=True)

    registered_at = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    # "active" | "completed". Kept as a string rather than an enum so
    # future states (e.g. "abandoned") can slot in without a migration.
    status = db.Column(db.String(32), nullable=False, default="active")

    # Free-form payload. /complete stores the outcome dict here so
    # later phases (compliance, learning) can mine it without another
    # table.
    extra = db.Column(db.JSON, default=dict)

    def __init__(self, **kwargs):
        kwargs.setdefault("registered_at", datetime.now(timezone.utc))
        kwargs.setdefault("status", "active")
        if kwargs.get("extra") is None:
            kwargs["extra"] = {}
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "consumer": self.consumer,
            "repo": self.repo,
            "worktree_path": self.worktree_path,
            "hint": self.hint,
            "registered_at": iso_utc(self.registered_at),
            "completed_at": iso_utc(self.completed_at),
            "status": self.status,
            "extra": self.extra or {},
        }
