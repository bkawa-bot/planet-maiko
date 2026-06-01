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


_MAX_REVIEW_ROUNDS = 3


def _parse_verdict(text):
    """Pull a reviewer's verdict from its ready_for_review artifact, which
    the review protocol starts with `VERDICT: <tag>`. Returns one of
    approve | approve_with_comments | soft_block | hard_block, or None when
    there's no verdict line (then the node isn't a review-loop node)."""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s[:8].upper() == "VERDICT:":
            v = s[8:].strip().lower()
            for tag in ("hard_block", "soft_block", "approve_with_comments", "approve"):
                if tag in v:
                    return tag
            return v or None
    return None


def _review_feedback(reviewer_job, verdict):
    """The revision prompt handed back to the coder: the reviewer's
    artifact (verdict + summary) plus its inline comments, if any."""
    parts = [
        f"A reviewer looked at your work and asked for changes (verdict: "
        f"{verdict}). Address the feedback below in your existing worktree, "
        f"commit, then reply --type ready_for_review again.",
        f"## Review\n\n{reviewer_job.artifact or '(no summary)'}",
    ]
    try:
        from planet_maiko.models.diff_comment import DiffComment
        comments = DiffComment.query.filter_by(job_id=reviewer_job.id).all()
        if comments:
            lines = []
            for c in comments:
                body = getattr(c, "body", None) or getattr(c, "content", "") or ""
                lines.append(f"- `{c.file_path}:{c.line_number}`: {body}")
            parts.append("## Inline comments\n\n" + "\n".join(lines))
    except Exception as e:
        logger.debug(f"[cycle] review comment fetch failed: {e}")
    return "\n\n".join(parts)


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


def _emit_ready_memos(run, nrs_by_node):
    """When a run finishes, drop one 'ready to review + PR' memo per coder
    branch (a done diff-producer that isn't a reviewer), so each fanned
    branch lands in the human review queue as its own PR-to-be. Reuses the
    diff-review surface; the coder job is a normal AgentJob with a worktree
    + branch, so /jobs/<id>?view=diff and open-PR work as usual.

    Best-effort; runs once (the run leaves "running" right after)."""
    from planet_maiko.database import db
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.models.workflow import Workflow
    from planet_maiko.agent_types import get_agent_type
    from planet_maiko.brain.memos import create_memo
    try:
        wf = db.session.get(Workflow, run.workflow_id)
        flow_name = (wf.name if wf else None) or "a flow"
        for insts in nrs_by_node.values():
            for nr in insts:
                if nr.status != "done" or not nr.agent_job_id:
                    continue
                job = db.session.get(AgentJob, nr.agent_job_id)
                if not job or not (job.worktree_path and job.branch):
                    continue
                at = get_agent_type(job.kind)
                accepts = (at.accepts if at else None) or []
                out_kind = (at.output_kind if at else None) or "diff"
                # A coder: produces a diff but doesn't consume one (a reviewer
                # accepts "diff"). Only coders carry a PR-able branch.
                if out_kind != "diff" or "diff" in accepts:
                    continue
                label = (nr.extra or {}).get("label")
                tail = f": {label}" if label else ""
                create_memo(
                    kind="flow_diff_ready",
                    category="waiting",
                    title=f"Branch ready to review in {flow_name}",
                    body=(
                        f"Branch `{job.branch}`{tail} was reviewed by the flow. "
                        f"Open the diff to look it over and turn it into a PR."
                    ),
                    cta_label="Review the diff",
                    priority="normal",
                    extra={
                        "job_id": job.id,
                        "workflow_run_id": run.id,
                        "node_id": nr.node_id,
                        "branch": job.branch,
                    },
                )
    except Exception as e:
        logger.warning(f"[cycle] ready memos skipped for run {run.id}: {e}")


def _phase_advance_workflows():
    from planet_maiko.database import db
    from planet_maiko.models.workflow_run import WorkflowRun, NodeRun
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.orchestration import maybe_spawn
    from planet_maiko.agent_types import get_agent_type
    # Reuse the human-review transport: a "review" inbox message + the
    # standard resume, the same path the diff-review UI uses.
    from planet_maiko.api.diff_api import _resume_agent_with_review
    from planet_maiko.models.agent_message import AgentMessage

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

            # 1.5 Review loop. A node that emitted a blocking VERDICT
            #     (soft_block / hard_block) sends its upstream coder back to
            #     revise (wake the same session so it keeps its branch) and
            #     re-arms itself for another pass, bounded by
            #     _MAX_REVIEW_ROUNDS. approve / approve_with_comments (or no
            #     verdict) settles the node so downstream proceeds.
            for node in nodes:
                nid = node.get("id")
                inbound_for = [e.get("source") for e in edges if e.get("target") == nid]
                for rv in nrs_by_node.get(nid, []):
                    if rv.status != "done" or not rv.agent_job_id:
                        continue
                    if (rv.extra or {}).get("loop_settled"):
                        continue
                    rv_job = db.session.get(AgentJob, rv.agent_job_id)
                    verdict = _parse_verdict(rv_job.artifact) if rv_job else None
                    if verdict not in ("soft_block", "hard_block"):
                        rv.extra = {**(rv.extra or {}), "loop_settled": True}
                        continue
                    # Which coder to send back to. A fanned reviewer instance
                    # carries the exact paired coder NodeRun id (set at
                    # propagation); a 1:1 reviewer resolves its single upstream
                    # coder from the graph edge. Either way the id is derived,
                    # never sent by an agent.
                    coder_nr = coder_job = None
                    paired = (rv.extra or {}).get("paired_to")
                    if paired:
                        cnr = db.session.get(NodeRun, paired)
                        cj = (
                            db.session.get(AgentJob, cnr.agent_job_id)
                            if cnr and cnr.agent_job_id else None
                        )
                        if cj and cj.worktree_path:
                            coder_nr, coder_job = cnr, cj
                    else:
                        for src in inbound_for:
                            src_insts = nrs_by_node.get(src, [])
                            if len(src_insts) != 1:
                                continue
                            src_nr = src_insts[0]
                            cj = (
                                db.session.get(AgentJob, src_nr.agent_job_id)
                                if src_nr.agent_job_id else None
                            )
                            if cj and cj.worktree_path:
                                coder_nr, coder_job = src_nr, cj
                                break
                    round_n = (rv.extra or {}).get("round", 0)
                    if not coder_job or round_n >= _MAX_REVIEW_ROUNDS:
                        rv.extra = {
                            **(rv.extra or {}),
                            "loop_settled": True,
                            "loop_result": "max_rounds" if coder_job else "no_target",
                        }
                        continue
                    # Hand the review back through the SAME path a human review
                    # uses: a to_agent "review" inbox message + the standard
                    # resume. The coder reads its inbox, addresses the comments,
                    # commits, and replies ready_for_review. The workflow only
                    # decides WHEN to send one back (verdict + round cap).
                    db.session.add(AgentMessage(
                        task_id=coder_job.id,
                        direction="to_agent",
                        sender="reviewer",
                        content=_review_feedback(rv_job, verdict),
                        message_type="review",
                    ))
                    db.session.commit()
                    if not _resume_agent_with_review(coder_job.id, coder_job.worktree_path):
                        rv.extra = {
                            **(rv.extra or {}),
                            "loop_settled": True,
                            "loop_result": "wake_failed",
                        }
                        continue
                    # Coder iterates again on its branch; this reviewer instance
                    # waits to re-review (paired_to preserved, so it re-targets
                    # the same coder next round).
                    coder_job.status = "running"
                    coder_nr.status = "running"
                    coder_nr.extra = {**(coder_nr.extra or {}), "round": round_n + 1}
                    rv.status = "pending"
                    rv.agent_job_id = None
                    rv.extra = {**(rv.extra or {}), "round": round_n + 1}
                    advanced += 1

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

            def _compose_block(src_nr, src_job):
                """The prompt block for one upstream instance: push + fetch
                instructions for a diff producer, or its text artifact.
                Returns (block_or_None, push_failed)."""
                stype = get_agent_type(src_job.kind)
                out_kind = (stype.output_kind if stype else None) or "diff"
                if out_kind == "diff" and src_job.worktree_path and src_job.branch:
                    if not _push_branch(src_job.worktree_path, src_job.branch):
                        return None, True
                    base = _default_branch(src_job.worktree_path)
                    return (
                        f"## Code to review\n\n"
                        f"The upstream {src_nr.agent_type} step pushed its work to "
                        f"origin on branch `{src_job.branch}` (base `{base}`). There "
                        f"is no PR; review the branch directly. Fetch it into your "
                        f"worktree and read the diff:\n\n"
                        f"```bash\n"
                        f"git fetch origin {src_job.branch}\n"
                        f"git checkout {src_job.branch}\n"
                        f"git diff origin/{base}...HEAD\n"
                        f"```",
                        False,
                    )
                if src_job.artifact:
                    return (
                        f"## Input from the {src_nr.agent_type} step\n\n{src_job.artifact}",
                        False,
                    )
                return None, False

            # 2. Spawn ready nodes. A node acts only while it's an untouched
            #    placeholder (all its instances still pending); once spawned
            #    or fanned out it's skipped here.
            for node in nodes:
                nid = node.get("id")
                insts = nrs_by_node.get(nid, [])
                pendings = [i for i in insts if i.status == "pending"]
                if not pendings:
                    continue
                role = pendings[0].agent_type
                inbound = [e.get("source") for e in edges if e.get("target") == nid]

                # CASE A: re-armed fanned reviewer instances (they carry a
                #   paired_to from propagation). Each re-reviews its own paired
                #   coder's freshly revised branch, the moment THAT coder lands
                #   (per-instance, independent of its siblings). A 1:1 reviewer
                #   has no paired_to and falls through to the normal spawn.
                rearmed = [p for p in pendings if (p.extra or {}).get("paired_to")]
                if rearmed:
                    for p in rearmed:
                        cnr = db.session.get(NodeRun, p.extra["paired_to"])
                        if cnr is None:
                            p.status = "failed"
                            p.error = "lost its paired coder"
                            continue
                        if cnr.status in ("failed", "skipped"):
                            p.status = "skipped"
                            p.error = "its coder didn't finish the revision"
                            continue
                        if cnr.status != "done":
                            continue  # its coder is still revising; wait
                        cj = (
                            db.session.get(AgentJob, cnr.agent_job_id)
                            if cnr.agent_job_id else None
                        )
                        if not cj:
                            p.status = "failed"
                            p.error = "lost its paired coder"
                            continue
                        block, pf = _compose_block(cnr, cj)
                        if pf:
                            p.status = "failed"
                            p.error = "couldn't push the upstream branch for review"
                            continue
                        job = _spawn_job(role, block, nid)
                        p.status = "queued"
                        p.agent_job_id = job.id
                        advanced += 1
                    continue

                # CASE B: an untouched node (initial spawn / fan-out) or a 1:1
                #   reviewer re-arm. Gate on every inbound being terminal.
                placeholder = pendings[0]
                # A fully-failed upstream blocks this node (and its dependents
                # next tick). A partial upstream (some instances succeeded)
                # still flows.
                if any(node_status(src) == "failed" for src in inbound):
                    for p in pendings:
                        p.status = "skipped"
                        p.error = "an upstream step did not finish"
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

                # --- Propagation: when this node's single upstream is itself
                #     scattered (N done instances), the fan carries through —
                #     one instance here per upstream instance, paired 1:1 — so
                #     each fanned coder gets its own reviewer on its own diff. ---
                scattered_srcs = [
                    src for src in inbound
                    if len([i for i in nrs_by_node.get(src, []) if i.status == "done"]) > 1
                ]
                if len(inbound) == 1 and len(scattered_srcs) == 1:
                    src_dones = [
                        i for i in sorted(
                            nrs_by_node.get(scattered_srcs[0], []),
                            key=lambda x: (x.extra or {}).get("instance", 0),
                        )
                        if i.status == "done" and i.agent_job_id
                    ]
                    for idx, src_nr in enumerate(src_dones):
                        src_job = db.session.get(AgentJob, src_nr.agent_job_id)
                        if not src_job:
                            continue
                        target = placeholder if idx == 0 else NodeRun(
                            workflow_run_id=run.id, node_id=nid, agent_type=role,
                        )
                        if idx > 0:
                            db.session.add(target)
                        block, pf = _compose_block(src_nr, src_job)
                        if pf:
                            target.status = "failed"
                            target.error = "couldn't push the upstream branch for review"
                            continue
                        job = _spawn_job(role, block, nid)
                        target.status = "queued"
                        target.agent_job_id = job.id
                        target.extra = {
                            "instance": (src_nr.extra or {}).get("instance", idx),
                            "paired_to": src_nr.id,
                            "label": (src_nr.extra or {}).get("label") or f"#{idx + 1}",
                        }
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
                        block, pf = _compose_block(src_nr, src_job)
                        if pf:
                            push_failed = True
                            break
                        if block:
                            blocks.append(block)

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
                _emit_ready_memos(run, nrs_by_node)

        db.session.commit()
        return {"advanced": advanced}
    except Exception as e:
        logger.warning(f"[cycle] advance workflows skipped: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"advanced": 0, "error": str(e)}
