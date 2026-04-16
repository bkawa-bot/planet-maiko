"""Insight model — tribal/operational notes injected into CLAUDE.md.

Distinct from Learning. A Learning is a coding rule (goes through
signals + clustering + confidence + LoRA training). An Insight is a
piece of operational context an agent would benefit from knowing
before starting work on a repo:

    - "Use IntelliJ to run tests in this repo, the CLI runner is broken."
    - "The personalization repo is mid-migration — schema column names
      don't match ORM field names yet."
    - "Slack channel #auth-team has the context if you're touching
      session handling."

These never feed the LoRA trainer. They're a read-only playbook that
every new agent session inherits via CLAUDE.md at worktree prep time,
scoped to repo.
"""

from datetime import datetime, timezone
from planet_maiko.database import db


class Insight(db.Model):
    __tablename__ = "insights"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # The note itself. Kept short (one sentence or a short paragraph) —
    # Insights are CLAUDE.md fodder, not documentation.
    text = db.Column(db.Text, nullable=False)

    # Repo scope: if set, only agents working in this repo see the
    # insight. Null = global (every agent sees it). Key the playbook
    # view off this.
    repo_scope = db.Column(db.String(256), nullable=True, index=True)

    # Free-form tags. Rendered as chips in the UI, used for filtering.
    # E.g. ["tooling", "migration", "team"].
    tags = db.Column(db.JSON, default=list)

    # Who surfaced this. agent_id if an agent reported it via MCP
    # reply(message_type="insight"), null if the user typed it directly.
    author_agent_id = db.Column(db.String(128), nullable=True, index=True)

    # pending  — agent-reported, waiting for user approval
    # active   — approved, injected into every agent's CLAUDE.md
    # dismissed — user rejected; not shown, not injected
    status = db.Column(db.String(20), default="pending", index=True)

    # Optional TTL for state-in-flight notes ("mid-migration until end
    # of Q2"). Expired insights stay in the DB but are skipped by the
    # injector and visually dimmed in the UI so the user can revive or
    # delete them.
    expires_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    # Updated every time an agent or the user re-confirms the insight
    # is still true. Lets the UI sort by "most recently confirmed" so
    # stale notes sink.
    last_confirmed_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def is_expired(self, now=None):
        if not self.expires_at:
            return False
        now = now or datetime.now(timezone.utc)
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now >= expires

    def to_dict(self):
        return {
            "id": self.id,
            "text": self.text,
            "repo_scope": self.repo_scope,
            "tags": self.tags or [],
            "author_agent_id": self.author_agent_id,
            "status": self.status,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_confirmed_at": self.last_confirmed_at.isoformat() if self.last_confirmed_at else None,
            "expired": self.is_expired(),
        }
