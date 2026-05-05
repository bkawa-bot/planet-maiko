from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class AgentProfile(db.Model):
    """Persistent agent identity with stats and specialization.

    Agents are characters in your town - they have names, avatars,
    personalities, and grow through experience. The profile persists
    across sessions and tracks their history.
    """
    __tablename__ = "agent_profiles"

    id = db.Column(db.String(128), primary_key=True)
    display_name = db.Column(db.String(100), nullable=False)
    # Card id (matches a row in data/cards/cards.yaml). Set by
    # create_profile via roll_card(); the model default is None
    # because the breed-string default ("shiba") was a leftover
    # from before the cards system, and CardAvatar's procedural
    # fallback handles a missing card id gracefully.
    avatar = db.Column(db.String(50), nullable=True)
    flavor_text = db.Column(db.String(256), nullable=True)  # "Loves debugging. Afraid of CSS."

    # Orchestration identity. Role picks what kind of work this agent takes
    # ("coding" default for backward compat; "review" / "investigation" for
    # the new roles). scope_repo narrows it to a single repo — null means
    # "global" (e.g. the Detective for cross-repo incidents).
    role = db.Column(db.String(32), default="coding", index=True)
    scope_repo = db.Column(db.String(256), nullable=True, index=True)
    # Markdown injected into every session this agent runs — the "soul" of
    # the agent. Analogous to AGENTS.md / CLAUDE.md but per-profile.
    instructions = db.Column(db.Text, nullable=True)

    # Attached specialties (CustomSkill IDs). Specialties are optional
    # domain-context chunks the agent can pick up per run. Role drives
    # runtime behavior (dispatch, protocol template). Specialty picked
    # for a given run adds extra prompt into CLAUDE.md on top of the
    # role protocol. No specialty picked → base role context only.
    specialty_ids = db.Column(db.JSON, default=list)

    # Stats
    tasks_completed = db.Column(db.Integer, default=0)
    tasks_failed = db.Column(db.Integer, default=0)
    prs_merged = db.Column(db.Integer, default=0)
    prs_changes_requested = db.Column(db.Integer, default=0)
    learnings_contributed = db.Column(db.Integer, default=0)

    # The agent's proven set of learning IDs — built via training
    context_set = db.Column(db.JSON, default=list)

    # Flexible metadata (adapter_path, trained_on_examples, etc.)
    extra = db.Column(db.JSON, default=dict)

    archived = db.Column(db.Boolean, default=False)
    archived_at = db.Column(db.DateTime, nullable=True)

    # Live runtime state — distinct from task.status.
    #   idle     — no claude process running for this agent's task
    #   working  — wake_agent is currently running claude --resume
    #   stuck    — was "working" but hasn't produced a pupdate in a while
    # The wake orchestrator (agents/wake.py) flips working↔idle; the
    # cleanup job promotes stale "working" to "stuck".
    state = db.Column(db.String(16), default="idle", index=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_active_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "display_name": self.display_name,
            "avatar": self.avatar,
            "flavor_text": self.flavor_text,
            "role": self.role or "coding",
            "scope_repo": self.scope_repo,
            "instructions": self.instructions or "",
            "specialty_ids": self.specialty_ids or [],
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "prs_merged": self.prs_merged,
            "prs_changes_requested": self.prs_changes_requested,
            "learnings_contributed": self.learnings_contributed,
            "context_set": self.context_set or [],
            "extra": self.extra or {},
            "archived": self.archived or False,
            "state": self.state or "idle",
            "created_at": iso_utc(self.created_at),
            "last_active_at": iso_utc(self.last_active_at),
        }
