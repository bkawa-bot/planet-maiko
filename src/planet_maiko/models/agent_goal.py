"""AgentGoal — a durable intent held by a role or a specific agent.

Goals are the generalization of Stage 0's hardcoded cartographer check.
Instead of "the brain cycle has a Python function that detects stale
overviews", we store a row per (role, kind, scope_repo) and the goal
evaluator dispatches on `kind`. This lets us:

  - Toggle individual detections on/off without code changes.
  - Let Stage 2's gap detectors *propose* new goals that approving
    installs as rows — no redeploy to gain new autonomy.
  - Show the user what each agent is watching for.

Scope:
  - `role` is always set (goal applies to any agent of that role).
  - `agent_profile_id` is optional — when set, the goal is tied to that
    specific profile and only shows up on that agent's card. When null,
    it's a role-level goal visible on every agent of the role.
  - `scope_repo` narrows the goal to one repo (Stage 0's cartograph
    goals are repo-scoped). Null means global-to-role.

Actions:
  - `propose` — emits an agent_proposal pupdate for the user to approve.
    This is the only action Stage 1 supports; it's safe because nothing
    fires without the user's click.
  - `spawn` — spawn an agent directly. Reserved for later stages and
    read-only roles only.
"""

from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class AgentGoal(db.Model):
    __tablename__ = "agent_goals"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Role this goal applies to. Never null — every goal is "owned" by
    # a role so the evaluator knows which kind of agent would fulfill it.
    role = db.Column(db.String(50), nullable=False, index=True)

    # If set, goal is scoped to this specific profile (shows only on
    # that agent's card). If null, it's a role-level goal applying to
    # any agent of the role.
    agent_profile_id = db.Column(
        db.String(128),
        db.ForeignKey("agent_profiles.id"),
        nullable=True,
        index=True,
    )

    # What this goal watches for. Current kinds:
    #   "keep_overview_current" — cartographer refreshes stale overviews
    # Future kinds will be added as Stage 2 gap detectors ship.
    kind = db.Column(db.String(100), nullable=False, index=True)

    # Repo this goal is about (e.g., "org/repo"). Null for role-global goals.
    scope_repo = db.Column(db.String(256), nullable=True, index=True)

    # Trigger shape. Stage 1 only uses "condition" (threshold-based check
    # like stale_days > N). "cadence" (fire every N hours) is reserved.
    trigger_kind = db.Column(db.String(20), nullable=False, default="condition")

    # JSON blob with trigger-specific params. For keep_overview_current
    # with trigger_kind=condition: {"stale_days": 30}.
    trigger_config = db.Column(db.JSON, default=dict)

    # What to do when the trigger fires:
    #   "propose" — emit an agent_proposal pupdate (user gates execution)
    #   "spawn"   — spawn an agent directly (only for read-only kinds;
    #               reserved for later stages)
    action_kind = db.Column(db.String(20), nullable=False, default="propose")
    action_config = db.Column(db.JSON, default=dict)

    # active | paused | archived
    # - active: evaluator fires
    # - paused: user has silenced temporarily; hidden from normal lists
    # - archived: soft-deleted; kept for audit trail
    status = db.Column(db.String(20), nullable=False, default="active", index=True)

    # When the evaluator last emitted a proposal or spawn for this goal.
    # Null means "never fired yet".
    last_fired_at = db.Column(db.DateTime, nullable=True)

    # Where this row came from: "seed" (auto-seeded for configured
    # repos), "user" (hand-created via UI), "proposal" (approved gap
    # detector proposal — Stage 2+).
    created_by = db.Column(db.String(20), nullable=False, default="seed")

    # Free-form room for kind-specific metadata (e.g., the agent's
    # suggested wording for why this goal matters).
    extra = db.Column(db.JSON, default=dict)

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
            "role": self.role,
            "agent_profile_id": self.agent_profile_id,
            "kind": self.kind,
            "scope_repo": self.scope_repo,
            "trigger_kind": self.trigger_kind,
            "trigger_config": self.trigger_config or {},
            "action_kind": self.action_kind,
            "action_config": self.action_config or {},
            "status": self.status,
            "last_fired_at": iso_utc(self.last_fired_at),
            "created_by": self.created_by,
            "extra": self.extra or {},
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
