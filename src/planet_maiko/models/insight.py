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
from planet_maiko.database import db, iso_utc


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

    # When an agent submitted this insight via the Pack Insights ritual,
    # points at the source AgentMessage.id. Lets "drop" in the ritual
    # modal find the right row to dismiss without fuzzy matching.
    source_message_id = db.Column(db.Integer, nullable=True, index=True)

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
            "expires_at": iso_utc(self.expires_at),
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
            "last_confirmed_at": iso_utc(self.last_confirmed_at),
            "expired": self.is_expired(),
        }


def _fingerprint(text):
    """Stable-ish fingerprint for near-duplicate detection.

    Normalizes whitespace + casing + trailing punctuation and returns
    the first 120 characters. Agents re-running on a repo tend to
    resurface the same observations with minor wording jitter; a
    leading-120 fingerprint catches most of those while tolerating
    trailing prose variation.
    """
    import re
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    # Strip surrounding markdown / punctuation that varies run-to-run.
    normalized = normalized.strip("`*_#>-. \t")
    return normalized[:120]


def find_duplicate(text, repo_scope, tags=None):
    """Return an existing Insight that duplicates this one, or None.

    Match rules (all must hold):
      - Same repo_scope (None == None counts)
      - status in (pending, active) — dismissed rows don't block
      - Leading-120-char normalized fingerprint matches

    Tags are not part of the match — an agent might tag the same
    observation differently across runs, and we still want to collapse.
    The first match wins; caller is expected to refresh
    last_confirmed_at and (optionally) append the new source_message_id
    to the existing row instead of inserting.
    """
    fp = _fingerprint(text)
    if not fp:
        return None
    candidates = (
        Insight.query
        .filter(Insight.status.in_(["pending", "active"]))
        .filter(Insight.repo_scope == repo_scope)
        .order_by(Insight.last_confirmed_at.desc())
        .limit(50)
        .all()
    )
    for ins in candidates:
        if _fingerprint(ins.text) == fp:
            return ins
    return None
