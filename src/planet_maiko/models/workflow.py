"""Workflow — a saved graph of role-steps (the flow editor's canvas).

A Workflow is the persisted node-graph a user builds in the flow
editor: which roles (AgentTypes) are the nodes, where they sit on the
canvas, and how their typed sockets are wired. The whole canvas is
stored as one JSON blob (the editor saves it as a unit), mirroring the
{kind, config} JSON convention the Automation model already uses.

Edge contract: an edge from A's output to B's input is valid when A's
output_kind matches B's input_kind. That check runs in the editor (and
later at run-prep); the DB does not enforce it. Running a workflow
(WorkflowRun + a per-node AgentJob) is a later phase; this model is the
saved graph only.
"""

import uuid
from datetime import datetime, timezone

from planet_maiko.database import db, iso_utc


def _new_workflow_id():
    return "wf-" + uuid.uuid4().hex[:10]


class Workflow(db.Model):
    __tablename__ = "workflows"

    id = db.Column(db.String(64), primary_key=True, default=_new_workflow_id)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # The canvas, saved as a unit by the editor:
    #   {"nodes": [{"id", "agent_type", "x", "y"}],
    #    "edges": [{"id", "source", "target",
    #               "sourceHandle", "targetHandle"}],
    #    "viewport": {"x", "y", "zoom"}}
    # JSON-primary on purpose: a node nudge is one cheap PATCH. Derived
    # node/edge rows (for queries like "which flows use role X") can be
    # added later when a query actually needs them.
    graph = db.Column(db.JSON, default=dict)

    # Tombstone. Workflows have no built-ins, so delete is always a
    # plain soft-delete (mirrors AgentType.deleted_at).
    deleted_at = db.Column(db.DateTime, nullable=True)

    # Forward-flex per the house pattern.
    extra = db.Column(db.JSON, default=dict)

    # Trigger watermark: the last time the trigger-eval phase looked at this
    # flow. Pupdates newer than this that match a trigger node fire a run; on
    # the first eval it's set to "now" so an armed flow consumes the existing
    # backlog silently instead of firing on every old matching pupdate.
    trigger_evaluated_at = db.Column(db.DateTime, nullable=True)

    # Arm/pause switch for trigger nodes. False (default) = saved but inert;
    # True = its triggers fire on matching pupdates. Saving a flow never
    # changes this, so you can build a trigger flow without it going live.
    trigger_armed = db.Column(db.Boolean, default=False)

    # Last time a SCHEDULE trigger on this flow fired (interval cadence). The
    # pupdate-trigger watermark above is advanced every eval; this one only
    # moves when a scheduled run actually starts.
    trigger_last_fired_at = db.Column(db.DateTime, nullable=True)

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
            "graph": self.graph or {"nodes": [], "edges": []},
            "extra": self.extra or {},
            "trigger_armed": bool(self.trigger_armed),
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
