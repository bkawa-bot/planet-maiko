"""Automation — the unified 'when → then' row that replaces the old
AgentGoal table and eventually the correlator CAUSE_CHAINS, rules.py
entries, and scheduled CustomSkills.

iPhone-Automations-shaped. Every row has:
  - one or more triggers (when[]) combined with when_logic ("all" / "any")
  - one or more actions (then[]) that run in order when the triggers match
  - a status, last-fired timestamp, and cooldown gating

Triggers and actions are structured dicts (kind + config), NOT free-form
prompts. The engine dispatches each on a small table of kind -> handler.
Keeping the LLM out of this layer is the whole point: you can see every
scheduled-or-conditional thing Maiko will ever do, edit it, pause it.

Supported kinds are documented in brain/automations/engine.py.
"""

from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class Automation(db.Model):
    __tablename__ = "automations"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Human-facing name and body. The name appears in the Automations
    # list + proposal inbox; the description is the short "what is this
    # for" blurb under it. Both are user-editable.
    name = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # Trigger definition. List of {kind, config} dicts. All conditions
    # must hold when when_logic == "all"; any is enough when "any".
    # `within_minutes` is for chain-type conditions that need a window
    # (e.g., "pupdate A and pupdate B within 30 min").
    when = db.Column(db.JSON, default=list)
    when_logic = db.Column(db.String(10), default="all")
    within_minutes = db.Column(db.Integer, nullable=True)

    # Action definition. List of {kind, config} dicts executed in
    # order when the triggers fire. Most automations have one action,
    # but a chain is supported (propose + nudge, for example).
    then = db.Column(db.JSON, default=list)

    # active | paused | archived
    # - active: engine evaluates + may fire
    # - paused: hidden from evaluation but kept for reference
    # - archived: soft-delete (filtered out of default lists)
    status = db.Column(db.String(20), default="active", index=True)

    last_fired_at = db.Column(db.DateTime, nullable=True)
    fire_count = db.Column(db.Integer, default=0)

    # user | seed | proposal
    # Seed rows are installed by the startup code for configured repos
    # (one cartographer watch per repo, for example). Proposal rows
    # come from gap-detector approvals. User rows are hand-authored.
    created_by = db.Column(db.String(20), default="user")

    # Optional association with a specific profile (null = any agent
    # of the role). Used for rendering on profile cards.
    agent_profile_id = db.Column(
        db.String(128),
        db.ForeignKey("agent_profiles.id"),
        nullable=True,
        index=True,
    )

    # Optional repo scope (also duplicated in when[] config when the
    # condition is repo-scoped — this column is for cheap filtering
    # without decoding JSON).
    scope_repo = db.Column(db.String(256), nullable=True, index=True)

    # Evaluation model:
    # - "cycle": evaluate once per brain cycle; cooldown_days gates
    #   re-firing. Used by stale-overview watches, incident chains,
    #   cadence-driven skill runs.
    # - "pupdate": iterate over each unprocessed pupdate; evaluate
    #   conditions against that specific pupdate. Actions operate on
    #   the matched pupdate (dismiss_pupdate / create_task_from_pupdate /
    #   complete_linked_task). First-match semantics, order by id.
    execution_scope = db.Column(db.String(20), nullable=False, default="cycle", index=True)

    # After a fire, wait this long before firing again even if the
    # condition still holds. Prevents re-nagging after a proposal
    # gets dismissed or the user takes action.
    cooldown_days = db.Column(db.Integer, default=7)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False,
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    agent_profile = db.relationship("AgentProfile")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "when": self.when or [],
            "when_logic": self.when_logic,
            "within_minutes": self.within_minutes,
            "then": self.then or [],
            "status": self.status,
            "last_fired_at": iso_utc(self.last_fired_at),
            "fire_count": self.fire_count or 0,
            "created_by": self.created_by,
            "agent_profile_id": self.agent_profile_id,
            "scope_repo": self.scope_repo,
            "execution_scope": self.execution_scope,
            "cooldown_days": self.cooldown_days,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
