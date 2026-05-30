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


def _phase_advance_workflows():
    from planet_maiko.database import db
    from planet_maiko.models.workflow_run import WorkflowRun
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
                blocks = []
                push_failed = False
                if not inbound:
                    # Root node: seed it with the human-provided kickoff
                    # task from the run (the "Task (input)" of the flow).
                    seed = (run.extra or {}).get("input")
                    if seed:
                        blocks.append(seed)
                else:
                    # Compose from upstream sources. A "diff" producer hands
                    # off through the remote: push its branch to origin and
                    # tell this step to fetch + review it (the reviewer
                    # already fetches a ref, diffs against base, and leaves
                    # local comments). Other kinds hand off their text
                    # artifact as a context section.
                    for src in inbound:
                        src_nr = by_node.get(src)
                        if not (src_nr and src_nr.agent_job_id):
                            continue
                        src_job = db.session.get(AgentJob, src_nr.agent_job_id)
                        if not src_job:
                            continue
                        src_type = get_agent_type(src_nr.agent_type)
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
                    nr.status = "failed"
                    nr.error = "couldn't push the upstream branch to origin for review"
                    continue

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
