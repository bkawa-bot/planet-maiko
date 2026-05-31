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
import subprocess
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Bound a pathological graph. A real flow is a handful of steps; this is
# a backstop, not an expected limit.
_MAX_NODES_PER_RUN = 50


def _git(args, cwd, timeout=60):
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _default_branch(worktree):
    """Best-effort default branch (the diff base). Falls back to 'main'
    when origin/HEAD isn't resolvable."""
    try:
        r = _git(["rev-parse", "--abbrev-ref", "origin/HEAD"], worktree)
        ref = (r.stdout or "").strip()
        if r.returncode == 0 and ref.startswith("origin/"):
            return ref.split("/", 1)[1]
    except Exception:
        pass
    return "main"


def _push_branch(worktree, branch):
    """Push a producer's branch to origin so a downstream reviewer can
    fetch the real diff. Idempotent (re-pushing is a fast no-op).
    Returns False on any failure (no remote, no auth, missing branch)."""
    try:
        r = _git(["push", "-u", "origin", branch], worktree, timeout=120)
        if r.returncode != 0:
            logger.warning(
                f"[cycle] workflow branch push failed ({branch}): "
                f"{(r.stderr or '').strip()[:200]}"
            )
            return False
        return True
    except Exception as e:
        logger.warning(f"[cycle] workflow branch push error ({branch}): {e}")
        return False


def _parse_tasks(text):
    """Split a decomposer's reply into one block per TASK: marker. Each
    block (the TASK: title line plus its description lines) is fed to one
    coder instance as its task. Returns [] when there are no markers."""
    if not text:
        return []
    tasks, cur = [], None
    for ln in text.splitlines():
        if ln.strip()[:5].upper() == "TASK:":
            if cur is not None:
                block = "\n".join(cur).strip()
                if block:
                    tasks.append(block)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
    if cur is not None:
        block = "\n".join(cur).strip()
        if block:
            tasks.append(block)
    return tasks


def _first_line(text, limit=80):
    """A short label for a scattered instance: the task's first real line,
    minus any TASK: marker, truncated."""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s:
            if s[:5].upper() == "TASK:":
                s = s[5:].strip()
            return s[:limit] or "task"
    return "task"


def _emit_gate_memo(run, node_id, nr, edges, nrs_by_node):
    """Park-time notification. When a gate starts waiting on the user,
    drop a 'waiting' memo carrying the upstream plan so it surfaces in
    the inbox review queue with inline approve / reject, instead of being
    buried in a run the user has to remember to reopen.

    Best-effort: a memo failure must never break the run. create_memo
    only adds to the session; the phase's own commit persists it."""
    from planet_maiko.database import db
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.models.workflow import Workflow
    from planet_maiko.brain.memos import create_memo
    try:
        up_job = None
        for src in [e.get("source") for e in edges if e.get("target") == node_id]:
            for src_nr in nrs_by_node.get(src, []):
                if src_nr.agent_job_id:
                    up_job = db.session.get(AgentJob, src_nr.agent_job_id)
                    if up_job:
                        break
            if up_job:
                break
        wf = db.session.get(Workflow, run.workflow_id)
        flow_name = (wf.name if wf else None) or "a flow"
        plan = ((up_job.artifact if up_job else None) or "").strip()
        create_memo(
            kind="flow_approval",
            category="waiting",
            title=f"Plan ready for your approval in {flow_name}",
            body=plan or "The upstream step left no readable output. Open the flow to review.",
            cta_label="Review and approve",
            priority="normal",
            extra={
                "workflow_run_id": run.id,
                "node_run_id": nr.id,
                "node_id": node_id,
                "job_id": up_job.id if up_job else None,
                "flow_name": flow_name,
            },
        )
    except Exception as e:
        logger.warning(f"[cycle] gate memo skipped for run {run.id}: {e}")


def _phase_advance_workflows():
    from planet_maiko.database import db
    from planet_maiko.models.workflow_run import WorkflowRun, NodeRun
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.orchestration import maybe_spawn
    from planet_maiko.agent_types import get_agent_type

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
            nrs_by_node = {}
            for nr in run.node_runs:
                nrs_by_node.setdefault(nr.node_id, []).append(nr)

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

            # Roll a graph node's N instances (scatter) into one state for
            # downstream readiness: "done" only when every instance is done,
            # "failed" when all are terminal with none done, "partial" on a
            # terminal mix, "active" while any is still in flight.
            def node_status(node_id):
                insts = nrs_by_node.get(node_id, [])
                if not insts:
                    return "absent"
                ss = [i.status for i in insts]
                if all(s == "done" for s in ss):
                    return "done"
                if all(s in ("done", "failed", "skipped") for s in ss):
                    return "partial" if any(s == "done" for s in ss) else "failed"
                return "active"

            scope_repo = (run.extra or {}).get("scope_repo")

            def _spawn_job(role, description, node_id):
                """Mint one queued AgentJob for a node (or a scatter
                instance). Lazy-spawns the (role, scope) profile so
                execute_jobs resolves the right role, including custom ones
                like planner/decomposer whose kind isn't in the builtin map."""
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
                    extra={"workflow_run_id": run.id, "node_id": node_id},
                )
                db.session.add(job)
                return job

            # 2. Spawn ready nodes. A node acts only while it's an untouched
            #    placeholder (all its instances still pending); once spawned
            #    or fanned out it's skipped here.
            for node in nodes:
                nid = node.get("id")
                insts = nrs_by_node.get(nid, [])
                if not insts or not all(i.status == "pending" for i in insts):
                    continue
                placeholder = insts[0]
                inbound = [e.get("source") for e in edges if e.get("target") == nid]

                # A fully-failed upstream blocks this node (and its dependents
                # next tick). A partial upstream (some instances succeeded)
                # still flows.
                if any(node_status(src) == "failed" for src in inbound):
                    placeholder.status = "skipped"
                    placeholder.error = "an upstream step did not finish"
                    continue
                if not all(node_status(src) in ("done", "partial") for src in inbound):
                    continue  # inputs not all terminal yet

                # Approval gate: a control node, not an agent. Park it for
                # the user. Approve marks it done + forwards its upstream's
                # job; reject skips it (and its dependents).
                if node.get("kind") == "gate" or node.get("agent_type") == "gate":
                    placeholder.status = "awaiting_approval"
                    _emit_gate_memo(run, nid, placeholder, edges, nrs_by_node)
                    continue

                role = placeholder.agent_type

                # Gather every done upstream instance + its job. Resolve the
                # output kind from the producing JOB's role (not the NodeRun's
                # agent_type) so it stays correct through a pass-through gate,
                # whose forwarded job is the real producer. A "tasks" producer
                # turns this node into a scatter.
                upstream = []
                scatter_src = None
                for src in inbound:
                    for src_nr in nrs_by_node.get(src, []):
                        if src_nr.status != "done" or not src_nr.agent_job_id:
                            continue
                        src_job = db.session.get(AgentJob, src_nr.agent_job_id)
                        if not src_job:
                            continue
                        upstream.append((src_nr, src_job))
                        st = get_agent_type(src_job.kind)
                        if st and st.output_kind == "tasks":
                            scatter_src = src_job

                # --- Scatter: one instance per emitted task. Reuse the
                #     placeholder as instance 0, mint NodeRuns for the rest;
                #     each instance gets exactly its own task as the prompt. ---
                if scatter_src is not None:
                    tasks = _parse_tasks(scatter_src.artifact)
                    if not tasks:
                        placeholder.status = "failed"
                        placeholder.error = "the upstream step produced no tasks to scatter"
                        continue
                    for idx, task_text in enumerate(tasks):
                        target = placeholder if idx == 0 else NodeRun(
                            workflow_run_id=run.id, node_id=nid, agent_type=role,
                        )
                        if idx > 0:
                            db.session.add(target)
                        job = _spawn_job(role, task_text, nid)
                        target.status = "queued"
                        target.agent_job_id = job.id
                        target.extra = {"instance": idx, "label": _first_line(task_text)}
                        advanced += 1
                    continue

                # --- Normal single spawn: compose the prompt from the seed
                #     (root) or the upstream artifacts / pushed branches. ---
                blocks = []
                push_failed = False
                if not inbound:
                    # Root node: seed it with the human-provided kickoff
                    # task from the run (the "Task (input)" of the flow).
                    seed = (run.extra or {}).get("input")
                    if seed:
                        blocks.append(seed)
                else:
                    for src_nr, src_job in upstream:
                        src_type = get_agent_type(src_job.kind)
                        out_kind = (src_type.output_kind if src_type else None) or "diff"
                        if out_kind == "diff" and src_job.worktree_path and src_job.branch:
                            if not _push_branch(src_job.worktree_path, src_job.branch):
                                push_failed = True
                                break
                            base = _default_branch(src_job.worktree_path)
                            blocks.append(
                                f"## Code to review\n\n"
                                f"The upstream {src_nr.agent_type} step pushed its work to "
                                f"origin on branch `{src_job.branch}` (base `{base}`). There "
                                f"is no PR; review the branch directly. Fetch it into your "
                                f"worktree and read the diff:\n\n"
                                f"```bash\n"
                                f"git fetch origin {src_job.branch}\n"
                                f"git checkout {src_job.branch}\n"
                                f"git diff origin/{base}...HEAD\n"
                                f"```"
                            )
                        elif src_job.artifact:
                            blocks.append(
                                f"## Input from the {src_nr.agent_type} step\n\n"
                                f"{src_job.artifact}"
                            )

                if push_failed:
                    placeholder.status = "failed"
                    placeholder.error = "couldn't push the upstream branch to origin for review"
                    continue

                description = "\n\n".join(blocks) if blocks else None
                job = _spawn_job(role, description, nid)
                placeholder.status = "queued"
                placeholder.agent_job_id = job.id
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
