"""AgentType — the role-level identity of an agent.

Split out from CustomSkill: a Specialty is the per-run prompt/context
the agent picks up; an AgentType is the role-level identity that
determines what protocol it reads, what permissions it has, what
output shape Maiko expects from it, and how it gets spawned.

The four built-ins (coding / review / investigation / cartographer)
are seeded as is_default=True rows on first boot from the bundled
prompt .md files. User-created custom agent types are first-class
rows alongside the built-ins — same shape, same API, no special-casing.

Issue #22: "More agent types / custom agents."
"""

from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class AgentType(db.Model):
    __tablename__ = "agent_types"

    # Stable slug. Built-in ids match the historical role strings
    # ("coding", "review", "investigation", "cartographer") so existing
    # AgentProfile.role values keep resolving. User-created types pick
    # their own slug.
    id = db.Column(db.String(64), primary_key=True)

    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    icon = db.Column(db.String(50), default="user")

    # Lifecycle. is_default marks the four built-ins so the seed pass
    # on boot can detect "is this a fresh install vs. one where the
    # user has customized." user_edited flips to True the first time
    # a user PATCHes a default so the seed pass stops overwriting
    # their changes. deleted_at is a tombstone for soft-deleted
    # defaults (so the next boot's seed pass skips them).
    is_default = db.Column(db.Boolean, default=False)
    user_edited = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime, nullable=True)

    # Protocol — the CLAUDE.md template the agent reads at session
    # start. NOT NULL: an AgentType without a protocol can't run.
    protocol_prompt = db.Column(db.Text, nullable=False)

    # Spawn shape. Collapsed from the prior needs_worktree +
    # requires_scope_repo_clone pair into one enum:
    #   "worktree" — real git worktree of scope_repo required. Fail
    #                fast at launch if no local clone is on disk
    #                (coding, review).
    #   "scratch"  — workspace on disk but no repo. prepare() mints a
    #                directory under <data_dir>/scratch-worktrees and
    #                lands TASK.md / CLAUDE.md / .mcp.json there
    #                (investigation, cartograph). Falls through if
    #                scope_repo is set but no clone exists.
    # A third "none" (no workspace) value is intentionally not
    # supported — specialty / CustomSkill covers that case.
    spawn_mode = db.Column(
        db.String(16), nullable=False, default="worktree",
    )
    # null | "plan" — runtime maps to its own permission flag.
    permission_mode = db.Column(db.String(32), nullable=True)

    # Output shape. "diff" = the agent produces a git diff the user
    # reviews and approves. "report" = the agent produces a markdown
    # report saved to task.extra.artifact. "insight" = the agent
    # produces an insight (the cartographer's repo overview path).
    output_kind = db.Column(db.String(20), default="diff")

    # routing.rules key used to resolve model + effort. Every built-in
    # uses "coding_agent" today (a long-standing TODO).
    model_routing_key = db.Column(db.String(64), default="coding_agent")

    # Forward-flex. Future per-type fields the schema doesn't have a
    # column for yet can land here without a migration.
    extra = db.Column(db.JSON, default=dict)

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "is_default": bool(self.is_default),
            "user_edited": bool(self.user_edited),
            "deleted_at": iso_utc(self.deleted_at),
            "protocol_prompt": self.protocol_prompt,
            "spawn_mode": self.spawn_mode or "worktree",
            "permission_mode": self.permission_mode,
            "output_kind": self.output_kind or "diff",
            "model_routing_key": self.model_routing_key or "coding_agent",
            "extra": self.extra or {},
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
