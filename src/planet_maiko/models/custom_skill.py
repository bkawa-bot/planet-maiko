from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class CustomSkill(db.Model):
    """User-editable skill prompt.

    Skills are prompt templates that the brain session executes.
    Default skills are seeded on first run. Users can edit them
    or create their own.

    The prompt can reference context variables like {pupdates},
    {tasks}, {calendar} and can mention MCPs to use.
    """
    __tablename__ = "custom_skills"

    id = db.Column(db.String(50), primary_key=True)  # e.g. "investigate"
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(256), nullable=True)
    prompt = db.Column(db.Text, nullable=False)
    mcps = db.Column(db.JSON, default=list)  # ["slack", "figma", "linear"]
    icon = db.Column(db.String(20), default="wand")  # lucide icon name
    is_default = db.Column(db.Boolean, default=False)  # shipped with Maiko
    user_edited = db.Column(db.Boolean, default=False)  # True once user edits the prompt
    # Does this specialty need a git worktree to do its work? True for
    # anything that reads actual code (investigate, repo-analysis,
    # cartograph-style specialties). False for narrative / analysis
    # specialties (brainstorm, plan, verify-level) that just compose
    # a prompt from DB state. Default off — opt-in per specialty.
    needs_worktree = db.Column(db.Boolean, default=False)
    # When set, this CustomSkill can act as a first-class agent type
    # (not just a specialty layered onto a built-in role). The string
    # is rendered into CLAUDE.md verbatim in place of the role-default
    # protocol (agent-protocol.md / review-agent-protocol.md / etc.).
    # `prompt` keeps its existing role: the specialty body appended as
    # "Your specialty for this run." Leaving this NULL means "I'm a
    # specialty layered onto a built-in role, not my own agent type."
    protocol_prompt = db.Column(db.Text, nullable=True)
    # Maps to the runtime's permission-mode flag (Claude Code:
    # `--permission-mode plan` for read-only-with-plan, otherwise
    # NULL). When set on a CustomSkill that's serving as an agent
    # type, overrides the per-role default in kickoff.py (which
    # hardcodes "plan" for cartographer + plan_first). Leaving this
    # NULL means "use whatever the role default is."
    permission_mode = db.Column(db.String(32), nullable=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    # Soft-delete tombstone for default skills. Hard delete is fine for
    # user-created skills, but defaults get re-seeded on every boot, so
    # the user's "delete this default" needs a flag the seed pass can
    # see and skip. NULL = active, set = deleted.
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "mcps": self.mcps,
            "icon": self.icon,
            "is_default": self.is_default,
            "user_edited": self.user_edited,
            "needs_worktree": bool(self.needs_worktree),
            "protocol_prompt": self.protocol_prompt or "",
            "permission_mode": self.permission_mode or "",
            "last_run_at": iso_utc(self.last_run_at),
            "deleted_at": iso_utc(self.deleted_at),
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
