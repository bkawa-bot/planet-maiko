"""CRUD for Workflow — saved flow-editor graphs. See models/workflow.py.

JSON-blob storage: the whole canvas is one `graph` field, PATCHed as a
unit whenever the editor saves. Running a workflow (a WorkflowRun plus a
per-node AgentJob) is a later phase; this blueprint is create / read /
update / delete of the saved graph only. Mirrors agent_types_api.py.
"""

import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from planet_maiko.database import db, iso_utc
from planet_maiko.models.workflow import Workflow
from planet_maiko.models.workflow_run import WorkflowRun, NodeRun

logger = logging.getLogger(__name__)
workflows_bp = Blueprint("workflows", __name__)


_EDITABLE_FIELDS = {"name", "description", "graph", "extra"}


@workflows_bp.route("/workflows", methods=["GET"])
def list_workflows():
    """List active (non-tombstoned) workflows, most recently edited first."""
    rows = (
        Workflow.query
        .filter(Workflow.deleted_at.is_(None))
        .order_by(Workflow.updated_at.desc())
        .all()
    )
    return jsonify([r.to_dict() for r in rows])


@workflows_bp.route("/workflows/<wf_id>", methods=["GET"])
def get_workflow(wf_id):
    row = db.session.get(Workflow, wf_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "Workflow not found"}), 404
    return jsonify(row.to_dict())


@workflows_bp.route("/workflows", methods=["POST"])
def create_workflow():
    """Create a workflow. Only `name` is required; the editor saves the
    full graph on the first PATCH, so a fresh workflow starts empty."""
    data = request.get_json() or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400

    row = Workflow(
        name=data["name"],
        description=data.get("description") or None,
        graph=data.get("graph") or {"nodes": [], "edges": []},
        extra=data.get("extra") or {},
    )
    db.session.add(row)
    db.session.commit()
    return jsonify(row.to_dict()), 201


@workflows_bp.route("/workflows/<wf_id>", methods=["PATCH"])
def update_workflow(wf_id):
    """Whole-canvas save. The editor PATCHes the full `graph` (plus any
    renamed name/description) on a debounced save."""
    row = db.session.get(Workflow, wf_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "Workflow not found"}), 404

    data = request.get_json() or {}
    for field in _EDITABLE_FIELDS:
        if field in data:
            value = data[field]
            # Empty description collapses to NULL for clean "no description"
            # semantics, matching agent_types_api._coerce.
            if field == "description" and value == "":
                value = None
            setattr(row, field, value)
    db.session.commit()
    return jsonify(row.to_dict())


@workflows_bp.route("/workflows/<wf_id>", methods=["DELETE"])
def delete_workflow(wf_id):
    """Soft-delete (tombstone). Workflows have no built-ins, so there is
    no resurrect path to worry about."""
    row = db.session.get(Workflow, wf_id)
    if row is None:
        return jsonify({"error": "Workflow not found"}), 404
    row.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"status": "deleted"})


@workflows_bp.route("/workflows/<wf_id>/run", methods=["POST"])
def run_workflow(wf_id):
    """Launch a workflow. Snapshots the current graph, mints a NodeRun
    per node (pending), and sets the run running. The advance_workflows
    cycle phase spawns the root nodes on the next tick and drives the
    rest. Optional body: {scope_repo} = the repo the steps run against."""
    row = db.session.get(Workflow, wf_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "Workflow not found"}), 404

    if not (row.graph or {}).get("nodes"):
        return jsonify({"error": "This flow has no steps to run"}), 400

    from planet_maiko import flows
    data = request.get_json() or {}
    run = flows.start_run(
        row,
        input=(data.get("input") or "").strip() or None,
        scope_repo=(data.get("scope_repo") or "").strip() or None,
    )
    return jsonify(run.to_dict()), 201


@workflows_bp.route("/workflows/<wf_id>/arm", methods=["POST"])
def arm_workflow(wf_id):
    """Arm or pause a flow's triggers. Armed = its trigger nodes fire on
    matching pupdates; paused = saved but inert. Arming resets the eval
    watermark to now, so it fires on new pupdates rather than the backlog
    that piled up while it was paused. Body: {armed: bool}."""
    row = db.session.get(Workflow, wf_id)
    if row is None or row.deleted_at is not None:
        return jsonify({"error": "Workflow not found"}), 404
    armed = bool((request.get_json() or {}).get("armed"))
    row.trigger_armed = armed
    if armed:
        row.trigger_evaluated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"ok": True, "trigger_armed": armed})


def _end_runtime_session(job_id):
    """Force-tear-down the agent's live session for this job across every
    instantiated runtime (tmux kills its bound session; headless is a
    no-op). Mirrors the outbox's terminal-reply teardown, minus the
    message-type gate."""
    try:
        from planet_maiko.agents import brain_session
        for name, runtime in list(brain_session._runtimes.items()):
            if runtime is None:
                continue
            try:
                runtime.end_session(job_id)
            except Exception as e:
                logger.warning(f"[workflows] {name}.end_session({job_id}) failed: {e}")
    except Exception as e:
        logger.warning(f"[workflows] end-session fan-out for {job_id} failed: {e}")


def _kill_inflight_jobs(run):
    """Stop every in-flight node job of a run: kill its session, mark the
    job cancelled. Returns how many were stopped. Caller commits."""
    from planet_maiko.models.agent_job import AgentJob
    stopped = 0
    for nr in run.node_runs:
        if nr.status in ("queued", "running") and nr.agent_job_id:
            job = db.session.get(AgentJob, nr.agent_job_id)
            if job and job.status in ("queued", "running"):
                _end_runtime_session(job.id)
                job.status = "cancelled"
                job.finished_at = datetime.now(timezone.utc)
                stopped += 1
    return stopped


@workflows_bp.route("/workflow-runs/<run_id>/stop", methods=["POST"])
def stop_workflow_run(run_id):
    """Force-stop a running flow: kill its in-flight node sessions and mark
    its jobs + non-terminal nodes + the run itself cancelled. The run drops
    out of the active list and the executor stops advancing it. Idempotent
    on an already-terminal run."""
    run = db.session.get(WorkflowRun, run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404
    if run.status != "running":
        return jsonify({"ok": True, "status": run.status, "already_terminal": True})
    stopped = _kill_inflight_jobs(run)
    for nr in run.node_runs:
        if nr.status not in ("done", "failed", "skipped"):
            nr.status = "cancelled"
    run.status = "cancelled"
    run.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    logger.info(f"[workflows] stopped run {run_id}: killed {stopped} session(s)")
    return jsonify({"ok": True, "status": "cancelled", "stopped_jobs": stopped})


@workflows_bp.route("/workflow-runs/<run_id>", methods=["DELETE"])
def delete_workflow_run(run_id):
    """Remove a run from history. If it's still running, stop it first (kill
    sessions) so deleting can't orphan a live agent."""
    run = db.session.get(WorkflowRun, run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404
    if run.status == "running":
        _kill_inflight_jobs(run)
    for nr in list(run.node_runs):
        db.session.delete(nr)
    db.session.delete(run)
    db.session.commit()
    logger.info(f"[workflows] deleted run {run_id}")
    return jsonify({"ok": True, "deleted": run_id})


@workflows_bp.route("/workflow-runs/<run_id>", methods=["GET"])
def get_workflow_run(run_id):
    """The run plus its per-node state, for the live canvas / status poll.

    Enriches each node with the assigned agent's avatar + name so the
    canvas can show WHO is working a step (and the inspector can link
    into that agent's live session)."""
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.agent_message import AgentMessage
    run = db.session.get(WorkflowRun, run_id)
    if run is None:
        return jsonify({"error": "Run not found"}), 404
    data = run.to_dict()
    for nr in data.get("node_runs", []):
        jid = nr.get("agent_job_id")
        if not jid:
            continue
        job = db.session.get(AgentJob, jid)
        if job and job.agent_profile_id:
            prof = db.session.get(AgentProfile, job.agent_profile_id)
            if prof:
                nr["agent_avatar"] = prof.avatar
                nr["agent_name"] = prof.display_name
        # The agent's latest status line (its boot-up / progress narration)
        # so the canvas can show WHAT it's doing, not just that it's busy.
        last_status = (
            AgentMessage.query
            .filter_by(task_id=jid, direction="from_agent", message_type="status")
            .order_by(AgentMessage.created_at.desc())
            .first()
        )
        if last_status and last_status.content:
            nr["agent_status"] = last_status.content.strip()[:140]
    return jsonify(data)


def _gate_node_run(run_id, node_run_id):
    """Resolve a (run, node_run) pair for a gate action, or an error tuple."""
    run = db.session.get(WorkflowRun, run_id)
    if run is None:
        return None, None, (jsonify({"error": "Run not found"}), 404)
    nr = db.session.get(NodeRun, node_run_id)
    if nr is None or nr.workflow_run_id != run.id:
        return None, None, (jsonify({"error": "Step not found"}), 404)
    if nr.status != "awaiting_approval":
        return None, None, (jsonify({"error": "This step is not awaiting approval"}), 400)
    return run, nr, None


def _retire_gate_memo(node_run_id):
    """Dismiss the inbox memo for a gate once it's approved or rejected
    (from the run view or the memo itself) so it stops surfacing. Caller
    commits."""
    from planet_maiko.models.memo import Memo
    from planet_maiko.brain.memos import mark_dismissed
    for m in (
        Memo.query
        .filter(Memo.kind == "flow_approval")
        .filter(Memo.status.in_(("pending", "seen")))
        .all()
    ):
        if (m.extra or {}).get("node_run_id") == node_run_id:
            mark_dismissed(m)


@workflows_bp.route("/workflow-runs/<run_id>/nodes/<node_run_id>/approve", methods=["POST"])
def approve_node(run_id, node_run_id):
    """Approve a paused gate. Mark it done and forward its upstream
    producer's job through it (pass-through), so the next cycle advances
    downstream reading the real artifact/branch."""
    run, nr, err = _gate_node_run(run_id, node_run_id)
    if err:
        return err

    graph = run.graph_snapshot or {}
    edges = graph.get("edges") or []
    for src_id in [e.get("source") for e in edges if e.get("target") == nr.node_id]:
        src_nr = (
            NodeRun.query
            .filter_by(workflow_run_id=run.id, node_id=src_id)
            .first()
        )
        if src_nr and src_nr.agent_job_id:
            nr.agent_job_id = src_nr.agent_job_id
            break

    nr.status = "done"
    _retire_gate_memo(nr.id)
    db.session.commit()
    return jsonify(run.to_dict())


@workflows_bp.route("/workflow-runs/<run_id>/nodes/<node_run_id>/reject", methods=["POST"])
def reject_node(run_id, node_run_id):
    """Reject at a gate: skip it, which cascades a skip to everything
    downstream and ends the run as partial/failed."""
    run, nr, err = _gate_node_run(run_id, node_run_id)
    if err:
        return err
    nr.status = "skipped"
    nr.error = "rejected at the gate"
    _retire_gate_memo(nr.id)
    db.session.commit()
    return jsonify(run.to_dict())


@workflows_bp.route("/workflow-runs", methods=["GET"])
def list_workflow_runs():
    """Recent runs across all flows, lightweight summary for the Flows
    tab so a run can be reopened after you navigate away from it."""
    rows = (
        WorkflowRun.query
        .order_by(WorkflowRun.created_at.desc())
        .limit(25)
        .all()
    )
    out = []
    for r in rows:
        nrs = r.node_runs
        wf = db.session.get(Workflow, r.workflow_id)
        out.append({
            "id": r.id,
            "workflow_id": r.workflow_id,
            "workflow_name": wf.name if wf else "(deleted flow)",
            "status": r.status,
            "created_at": iso_utc(r.created_at),
            "finished_at": iso_utc(r.finished_at),
            "steps_total": len(nrs),
            "steps_done": sum(1 for n in nrs if n.status == "done"),
            "awaiting": sum(1 for n in nrs if n.status == "awaiting_approval"),
        })
    return jsonify(out)
