"""Specialty — a per-run chunk of prompt/context the agent picks up.

Split out from CustomSkill: a Specialty is the swappable body that
gets appended to CLAUDE.md as "Your specialty for this run." Many
specialties can be attached to one AgentProfile (specialty_ids list);
the user picks which one applies at assign time.

Contrast with AgentType, which is the agent's role-level identity
(protocol, permissions, output shape, spawn pattern). One AgentType
per profile; many specialties attached to a profile.

Issue #22 split.
"""

from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class Specialty(db.Model):
    __tablename__ = "specialties"

    id = db.Column(db.String(64), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(256), nullable=True)
    # The body appended to CLAUDE.md when this specialty is the
    # per-run pick. Template vars ({current_date}, {user_name}, etc.)
    # get substituted by get_specialty_prompt at injection time, the
    # same way get_skill_prompt does today on CustomSkill.
    prompt = db.Column(db.Text, nullable=False)
    # MCP servers this specialty wants pre-enabled when picked.
    mcps = db.Column(db.JSON, default=list)
    icon = db.Column(db.String(50), default="wand")

    # is_default marks the specialties shipped with Maiko (investigate,
    # repo-analysis, pr-review). user_edited flips True the first time
    # a user PATCHes a default so the seed pass on next boot stops
    # overwriting their edits. deleted_at is a tombstone for
    # soft-deleted defaults (so the seed pass skips them).
    is_default = db.Column(db.Boolean, default=False)
    user_edited = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime, nullable=True)

    last_run_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
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
            "prompt": self.prompt,
            "mcps": self.mcps or [],
            "icon": self.icon,
            "is_default": bool(self.is_default),
            "user_edited": bool(self.user_edited),
            "deleted_at": iso_utc(self.deleted_at),
            "last_run_at": iso_utc(self.last_run_at),
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
