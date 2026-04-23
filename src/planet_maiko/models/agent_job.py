"""AgentJob — a pack-owned one-shot agent run.

Split from Task: Task is what the user owes (a todo, a bug, a PR review
request they got); AgentJob is what the pack is running for the user
(a cartograph walk, an investigation, a scheduled skill invocation).

The Tasks page lists Tasks. The Agents page lists AgentJobs.

Lifecycle:
    pending_approval  — automation said ask_first=true; waiting on user
    queued            — approved or created without needing approval;
                        cycle's execute phase will kick it off next tick
    running           — agent subprocess is live
    done              — agent replied ready_for_review / done; artifact stored
    failed            — exception or agent errored out
    cancelled         — user stopped it or pulled the plug

Sources:
    automation_id  — if spawned by an Automation firing
    source_task_id — if spawned by a Task (Stage D: coding task → coder job)
    created_by = "user" — if a human kicked it off directly (e.g. Cartograph
                          button in Playbook UI)
"""

from datetime import datetime, timezone
from planet_maiko.database import db, iso_utc


class AgentJob(db.Model):
    __tablename__ = "agent_jobs"

    id = db.Column(db.String(64), primary_key=True)

    # What this job is — matches task.type today (cartograph, investigation,
    # repo_analysis, or a skill name like brainstorm / verify).
    # The cycle's execute phase dispatches by this to pick the role.
    kind = db.Column(db.String(64), nullable=False, index=True)

    title = db.Column(db.String(512), nullable=False)
    description = db.Column(db.Text, nullable=True)
    scope_repo = db.Column(db.String(256), nullable=True, index=True)
    priority = db.Column(db.String(20), default="normal")

    # Provenance
    created_by = db.Column(db.String(20), nullable=False, default="automation")
    automation_id = db.Column(
        db.Integer, db.ForeignKey("automations.id"), nullable=True, index=True,
    )
    source_task_id = db.Column(
        db.String(64), db.ForeignKey("tasks.id"), nullable=True, index=True,
    )

    # Approval gating
    requires_approval = db.Column(db.Boolean, default=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_by = db.Column(db.String(20), nullable=True)  # "user" | "auto"

    # Execution state
    # pending_approval | queued | running | done | failed | cancelled
    status = db.Column(db.String(32), default="queued", index=True)

    agent_profile_id = db.Column(
        db.String(128), db.ForeignKey("agent_profiles.id"), nullable=True, index=True,
    )
    worktree_path = db.Column(db.String(1024), nullable=True)
    branch = db.Column(db.String(256), nullable=True)
    session_id = db.Column(db.String(128), nullable=True)

    # Output
    artifact = db.Column(db.Text, nullable=True)
    error = db.Column(db.Text, nullable=True)

    # Timing
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False,
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    # Free-form for kind-specific metadata
    extra = db.Column(db.JSON, default=dict)

    automation = db.relationship("Automation", foreign_keys=[automation_id])
    agent_profile = db.relationship("AgentProfile", foreign_keys=[agent_profile_id])

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "scope_repo": self.scope_repo,
            "priority": self.priority,
            "created_by": self.created_by,
            "automation_id": self.automation_id,
            "source_task_id": self.source_task_id,
            "requires_approval": bool(self.requires_approval),
            "approved_at": iso_utc(self.approved_at),
            "approved_by": self.approved_by,
            "status": self.status,
            "agent_profile_id": self.agent_profile_id,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "session_id": self.session_id,
            "artifact": self.artifact,
            "error": self.error,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
            "started_at": iso_utc(self.started_at),
            "finished_at": iso_utc(self.finished_at),
            "extra": self.extra or {},
        }
