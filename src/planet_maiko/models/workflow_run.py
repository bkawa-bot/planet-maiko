"""WorkflowRun + NodeRun — a single execution of a saved Workflow.

A WorkflowRun is one launch of a Workflow's graph. It pins an immutable
graph_snapshot at launch, so the run explains itself even if the
Workflow is edited afterward. Each node in the graph gets a NodeRun,
which links to the AgentJob that actually executes it via a plain
indexed string (the DiffComment.job_id pattern, no FK: a node sits
pending before its job exists, and a retry would swap in a new id).

The advance_workflows cycle phase drives the run: it spawns a node's
AgentJob once all of the node's inbound sources are done, feeds each
upstream artifact into the new job's prompt, and finishes the run when
every node is terminal. Authoring lives in models/workflow.py; this
module is execution state only.
"""

import uuid
from datetime import datetime, timezone

from planet_maiko.database import db, iso_utc


def _new_run_id():
    return "wr-" + uuid.uuid4().hex[:10]


def _new_node_run_id():
    return "nr-" + uuid.uuid4().hex[:10]


class WorkflowRun(db.Model):
    __tablename__ = "workflow_runs"

    id = db.Column(db.String(64), primary_key=True, default=_new_run_id)
    workflow_id = db.Column(
        db.String(64), db.ForeignKey("workflows.id"), nullable=False, index=True,
    )
    # queued | running | done | failed | partial | cancelled
    status = db.Column(db.String(32), default="running", index=True)
    # Immutable copy of the graph at launch (nodes + edges) so the run is
    # reproducible and explainable regardless of later edits to the flow.
    graph_snapshot = db.Column(db.JSON, default=dict)
    error = db.Column(db.Text, nullable=True)
    extra = db.Column(db.JSON, default=dict)
    # The pupdate that fired this run, when it was started by a trigger node
    # (null for a manually-run flow). Used for dedup (a pupdate fires a given
    # flow once) + to seed the entry node with the pupdate's content.
    triggering_pupdate_id = db.Column(db.String(64), nullable=True, index=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False,
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    finished_at = db.Column(db.DateTime, nullable=True)

    node_runs = db.relationship("NodeRun", backref="run")

    def to_dict(self):
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "graph_snapshot": self.graph_snapshot or {"nodes": [], "edges": []},
            "error": self.error,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
            "finished_at": iso_utc(self.finished_at),
            "node_runs": [nr.to_dict() for nr in self.node_runs],
        }


class NodeRun(db.Model):
    __tablename__ = "node_runs"

    id = db.Column(db.String(64), primary_key=True, default=_new_node_run_id)
    workflow_run_id = db.Column(
        db.String(64), db.ForeignKey("workflow_runs.id"), nullable=False, index=True,
    )
    node_id = db.Column(db.String(128), nullable=False)
    agent_type = db.Column(db.String(64), nullable=False)
    # pending | queued | running | done | failed | skipped
    status = db.Column(db.String(32), default="pending", index=True)
    # FK-less link to the executing AgentJob (DiffComment.job_id pattern).
    agent_job_id = db.Column(db.String(64), nullable=True, index=True)
    error = db.Column(db.Text, nullable=True)
    # Scatter bookkeeping: {instance: int, label: str} for fanned-out
    # instances sharing a node_id; empty {} for ordinary 1:1 nodes.
    extra = db.Column(db.JSON, default=dict, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False,
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "node_id": self.node_id,
            "agent_type": self.agent_type,
            "status": self.status,
            "agent_job_id": self.agent_job_id,
            "error": self.error,
            "extra": self.extra or {},
        }
