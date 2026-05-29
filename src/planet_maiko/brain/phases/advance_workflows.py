"""Brain-cycle phase: advance running WorkflowRuns.

Drives flow execution. For each running WorkflowRun:
  1. Sync each NodeRun's status from its AgentJob (done / failed / running).
  2. For each pending node whose inbound sources are all done, spawn its
     AgentJob (status=queued), feeding each upstream artifact into the
     prompt as a section. The existing execute_agent_jobs phase runs the
     queued jobs next (capped 2/tick, which also throttles a fan-out).
  3. When every node is terminal, finish the run.

Slice 1: linear / DAG auto-run with text-artifact handoff. Approval
gates, bounded reviewer->coder loops, and diff-branch handoff are later
slices. Safety: only pending->queued transitions spawn (idempotent), a
node spawns at most once (guarded on agent_job_id), an upstream failure
skips its dependents rather than hanging, and a per-run node cap bounds
a runaway graph.
"""

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Bound a pathological graph. A real flow is a handful of steps; this is
# a backstop, not an expected limit.
_MAX_NODES_PER_RUN = 50


def _phase_advance_workflows():
    from planet_maiko.database import db
    from planet_maiko.models.workflow_run import WorkflowRun
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.orchestration import maybe_spawn

    try:
        runs = (
            WorkflowRun.query
            .filter(WorkflowRun.status == "running")
            .limit(10)
            .all()
        )
        if not runs:
            return {"advanced": 0}

        advanced = 0
        for run in runs:
            graph = run.graph_snapshot or {}
            nodes = graph.get("nodes") or []
            edges = graph.get("edges") or []
            by_node = {nr.node_id: nr for nr in run.node_runs}

            if len(nodes) > _MAX_NODES_PER_RUN:
                run.status = "failed"
                run.error = f"flow has too many steps ({len(nodes)} > {_MAX_NODES_PER_RUN})"
                run.finished_at = datetime.now(timezone.utc)
                continue

            # 1. Sync finished jobs into NodeRun status.
            for nr in run.node_runs:
                if nr.status in ("queued", "running") and nr.agent_job_id:
                    job = db.session.get(AgentJob, nr.agent_job_id)
                    if job is None:
                        continue
                    if job.status == "done":
                        nr.status = "done"
                    elif job.status in ("failed", "cancelled"):
                        nr.status = "failed"
                        nr.error = job.error or job.status
                    elif job.status == "running":
                        nr.status = "running"

            done_ids = {nr.node_id for nr in run.node_runs if nr.status == "done"}
            failed_ids = {nr.node_id for nr in run.node_runs if nr.status in ("failed", "skipped")}
            scope_repo = (run.extra or {}).get("scope_repo")

            # 2. Spawn ready pending nodes.
            for node in nodes:
                nid = node.get("id")
                nr = by_node.get(nid)
                if nr is None or nr.status != "pending":
                    continue
                inbound = [e.get("source") for e in edges if e.get("target") == nid]
                # An upstream failure blocks this node (and, transitively,
                # its own dependents on the next tick).
                if any(src in failed_ids for src in inbound):
                    nr.status = "skipped"
                    nr.error = "an upstream step did not finish"
                    continue
                if not all(src in done_ids for src in inbound):
                    continue  # inputs not ready yet

                role = nr.agent_type
                # Fold each upstream artifact into the prompt as its own
                # section. This is the text handoff: a planner's plan or a
                # prior agent's report flows into this step as context.
                blocks = []
                for src in inbound:
                    src_nr = by_node.get(src)
                    if src_nr and src_nr.agent_job_id:
                        src_job = db.session.get(AgentJob, src_nr.agent_job_id)
                        if src_job and src_job.artifact:
                            blocks.append(
                                f"## Input from the {src_nr.agent_type} step\n\n"
                                f"{src_job.artifact}"
                            )
                description = "\n\n".join(blocks) if blocks else None

                # Lazy-spawn a profile for (role, scope) so execute_jobs
                # resolves the right role, including custom roles like
                # planner (its kind isn't in the builtin role map, but the
                # assigned profile's role wins).
                profile = maybe_spawn(role, scope_repo)
                job = AgentJob(
                    id=uuid.uuid4().hex[:24],
                    kind=role,
                    title=f"{role} step",
                    description=description,
                    scope_repo=scope_repo,
                    created_by="system",
                    agent_profile_id=profile.id,
                    status="queued",
                    extra={"workflow_run_id": run.id, "node_id": nid},
                )
                db.session.add(job)
                nr.status = "queued"
                nr.agent_job_id = job.id
                advanced += 1

            # 3. Finish the run when every node is terminal.
            statuses = [nr.status for nr in run.node_runs]
            terminal = {"done", "failed", "skipped"}
            if statuses and all(s in terminal for s in statuses):
                if any(s in ("failed", "skipped") for s in statuses):
                    run.status = "partial" if any(s == "done" for s in statuses) else "failed"
                else:
                    run.status = "done"
                run.finished_at = datetime.now(timezone.utc)

        db.session.commit()
        return {"advanced": advanced}
    except Exception as e:
        logger.warning(f"[cycle] advance workflows skipped: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"advanced": 0, "error": str(e)}
