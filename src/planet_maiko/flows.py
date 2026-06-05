"""Workflow run helpers — shared by the manual Run endpoint and the
trigger-eval phase so both start a run identically."""

from planet_maiko.database import db


def start_run(workflow, *, input=None, scope_repo=None, triggering_pupdate_id=None,
              task_id=None):
    """Create + start a WorkflowRun for a saved Workflow.

    Pins the graph snapshot, seeds run.extra with the input + repo, and mints
    a NodeRun per node: a trigger node starts ``done`` (it's the entry — the
    pupdate that fired it IS its output, carried in ``run.extra.input``),
    every other node starts ``pending``. ``task_id`` links the run back to the
    Task it was launched from (the caller also marks that task in progress).
    Returns the run, or None if the flow has no nodes. Caller strings should
    already be stripped."""
    from planet_maiko.models.workflow_run import WorkflowRun, NodeRun
    graph = workflow.graph or {"nodes": [], "edges": []}
    nodes = graph.get("nodes") or []
    if not nodes:
        return None
    run = WorkflowRun(
        workflow_id=workflow.id,
        status="running",
        graph_snapshot=graph,
        triggering_pupdate_id=triggering_pupdate_id,
        extra={
            "scope_repo": scope_repo or None,
            "input": input or None,
            "task_id": task_id or None,
        },
    )
    db.session.add(run)
    db.session.flush()  # assign run.id before the NodeRuns reference it
    trigger_ids = {n.get("id") for n in nodes if n.get("kind") == "trigger"}
    for n in nodes:
        nid = n.get("id")
        db.session.add(NodeRun(
            workflow_run_id=run.id,
            node_id=nid,
            agent_type=n.get("agent_type") or n.get("kind") or "node",
            status="done" if nid in trigger_ids else "pending",
        ))
    db.session.commit()
    return run
