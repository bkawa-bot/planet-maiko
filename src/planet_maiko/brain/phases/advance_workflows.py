"""Brain-cycle phase: advance running WorkflowRuns.

The executor is a dumb DAG runner. It knows nothing about "reviewers",
"coders", or "decomposers" — it moves data between nodes by role-agnostic
rules and lets the agents' declared types (accepts / output_kind) decide
what flows where. Each tick, for every running WorkflowRun:

  1. Sync each NodeRun's status from its AgentJob (done / failed / running).
  2. Revision loop: a done node that emitted a `revision_request` output
     sends that feedback back to the upstream node it depends on (resumes
     the same session so it keeps its branch) and re-arms itself for
     another pass, bounded by _MAX_REVIEW_ROUNDS. No revision_request and
     the node settles so downstream proceeds. This is the generic shape of
     the review->revise loop, with zero knowledge of "review".
  3. Spawn ready nodes (every inbound terminal). Three role-agnostic shapes:
       - Scatter: an upstream emitted N outputs whose type this node
         `accepts` => fan into N instances, one output each (a decomposer
         emitting N `task`s is just the common case).
       - Propagate: an upstream that itself scattered into N done instances
         => N instances here, paired 1:1 (each fanned coder gets its own
         reviewer on its own diff).
       - Single: compose the prompt from the seed (root) or the upstream
         artifacts / pushed branches.
  4. Approval gates park for the user; finish the run when every node is
     terminal, dropping a "ready to review" memo per producer branch.

Agents hand structured data forward by posting outputs (`maiko emit --type
<kind> "<content>"`, saved on AgentJob.outputs); the executor reads those,
never parsing free text. Legacy fallbacks (parse TASK: blocks, parse a
VERDICT: line) survive only for a producer that emitted no outputs. Safety:
only pending->queued transitions spawn (idempotent), a node spawns at most
once (guarded on agent_job_id), an upstream failure skips its dependents
rather than hanging, and a per-run node cap bounds a runaway graph.
"""

import logging
import re
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


# A task header: a line that, after any markdown / list decoration
# (#, *, -, digits, dots, spaces), starts with "TASK" plus a delimiter.
# Tolerant so "TASK:", "**TASK:**", "## TASK 2:", "1. TASK -" all count —
# LLMs decorate these inconsistently and the old strict line-start check
# missed every variant, so the scatter parsed zero tasks.
_TASK_HEADER = re.compile(r"^[\s>#*.\d-]*task\b\s*\d*\s*[:).\-]", re.IGNORECASE)


def _parse_tasks(text):
    """Split a decomposer's reply into one block per task header. Each block
    (the header line plus its following description lines) is fed to one
    coder instance as its task. Returns [] when there are no headers."""
    if not text:
        return []
    tasks, cur = [], None
    for ln in text.splitlines():
        if _TASK_HEADER.match(ln):
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
    with the TASK header decoration stripped, truncated."""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s:
            s = _TASK_HEADER.sub("", s).strip(" *#:->") or s
            return s[:limit] or "task"
    return "task"


def _matching_outputs(job, accepts):
    """The structured outputs this job posted (via `maiko emit`) whose type
    the consuming node accepts, as full {type, content, title?, repo?}
    dicts. This is the generic fan-out signal: a producer that emitted N of
    what the next node consumes scatters into N instances. Role-agnostic —
    task / plan / report / anything — decided by the consumer's declared
    `accepts`, never by who produced it or a hardcoded "tasks" kind."""
    acc = set(accepts or ())
    return [
        o
        for o in (job.outputs or [])
        if isinstance(o, dict) and o.get("type") in acc and o.get("content")
    ]


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


def _revision_request(job):
    """The revision feedback if this node asked for changes, else None.

    Primary path: a structured `revision_request` output the node posted
    via `maiko emit` — its content IS the feedback to hand back, and the
    engine never reads the node's role or parses a verdict. Fallback: a
    legacy reviewer that wrote a blocking `VERDICT:` line into its artifact
    instead of emitting an output (pre-output protocol)."""
    if job is None:
        return None
    for o in reversed(job.outputs or []):
        if isinstance(o, dict) and o.get("type") == "revision_request" and o.get("content"):
            return o["content"]
    if _parse_verdict(job.artifact) in ("soft_block", "hard_block"):
        return job.artifact or "Address the review feedback and revise."
    return None


def _loop_signal(job):
    """The 'run another round' feedback from a loop's source node, or None.

    Primary path: a generic loop-control bit the node set via
    `maiko request-changes "<feedback>"` (stored at job.extra.loop_request).
    It is a plain continue + payload signal — NOT an output type or a role.
    Absent it, the node is done and the run proceeds along its forward edges.
    Falls back to a legacy revision_request output / VERDICT: line so an
    un-migrated reviewer still loops."""
    if job is None:
        return None
    lr = (job.extra or {}).get("loop_request")
    if isinstance(lr, dict) and lr.get("feedback"):
        return lr["feedback"]
    if lr:
        return "Address the requested changes and revise."
    return _revision_request(job)


def _revision_message(feedback_text, requester_job):
    """The prompt handed back to the producer when a downstream node asks
    for changes: the revision feedback plus any inline comments. Sent
    through the same inbox + resume transport a human diff-review uses, so
    the producer keeps its worktree/branch and just iterates."""
    parts = [
        "A downstream step reviewed your work and asked for changes. "
        "Address the feedback below in your existing worktree, commit, then "
        "reply --type ready_for_review again.",
        f"## Requested changes\n\n{feedback_text or '(no detail)'}",
    ]
    try:
        from planet_maiko.models.diff_comment import DiffComment
        comments = DiffComment.query.filter_by(job_id=requester_job.id).all()
        if comments:
            lines = []
            for c in comments:
                body = getattr(c, "body", None) or getattr(c, "content", "") or ""
                lines.append(f"- `{c.file_path}:{c.line_number}`: {body}")
            parts.append("## Inline comments\n\n" + "\n".join(lines))
    except Exception as e:
        logger.debug(f"[cycle] revision comment fetch failed: {e}")
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


def _run_action_node(node, input_text, run):
    """Run a fire-and-forget action node inline (memo / task / close linked
    task). Best-effort: a failure logs but never breaks the run. run_agent_job
    is NOT handled here: it's an awaited step spawned in the run loop (like a
    role node), not an inline side-effect."""
    cfg = node.get("config") or {}
    subtype = (cfg.get("subtype") or "create_memo").strip()
    try:
        if subtype == "create_task":
            _action_create_task((cfg.get("title") or "").strip(), input_text, run)
        elif subtype == "complete_linked_task":
            _action_complete_linked_task(run)
        else:
            _action_create_memo(cfg, input_text, run)
    except Exception as e:
        logger.warning(f"[cycle] action node ({subtype}) failed for run {run.id}: {e}")


def _spawn_run_agent_job(cfg, description, run, node_id):
    """Mint a queued AgentJob for a "run a skill" action STEP (kind = the
    chosen skill / builtin). Unlike the fire-and-forget memo/task actions,
    the caller links this job to the NodeRun and awaits it, so the skill's
    output flows downstream like a role node's. No agent_profile_id: the
    execute phase resolves the role from kind (skills via CustomSkill),
    exactly as it does for an automation's run_agent_job. Returns the job
    uncommitted (the phase's own commit persists it, after the NodeRun is
    linked)."""
    from planet_maiko.models.agent_job import AgentJob
    kind = (cfg.get("job_kind") or cfg.get("kind") or "").strip() or "cartograph"
    repo = (cfg.get("repo") or "").strip() or (run.extra or {}).get("scope_repo")
    priority = (cfg.get("priority") or "normal").strip() or "normal"
    title = (cfg.get("title") or "").strip() or f"{kind} (flow)"
    job = AgentJob(
        id=f"job-{uuid.uuid4().hex[:10]}",
        kind=kind,
        title=title,
        description=description,
        scope_repo=repo,
        priority=priority,
        created_by="flow",
        requires_approval=False,
        status="queued",
        approved_by="auto",
        approved_at=datetime.now(timezone.utc),
        extra={"workflow_run_id": run.id, "node_id": node_id, "source": "flow"},
    )
    db.session.add(job)
    return job


def _action_create_memo(cfg, body, run):
    """Drop a memo. config carries title / priority / url; the body is the
    input handed to the node. When the run was fired by a pupdate, the title /
    url / body default to that pupdate's (so a [pupdate] -> [notify] flow
    surfaces the event faithfully, matching the old notify_me automation with
    no {placeholder} templating). Deduped on the triggering pupdate."""
    from planet_maiko.database import db
    from planet_maiko.brain.memos import create_memo
    title = (cfg.get("title") or "").strip()
    url = (cfg.get("url") or "").strip() or None
    priority = (cfg.get("priority") or "normal").strip() or "normal"
    body_text = (body or "").strip()
    pid = run.triggering_pupdate_id or None
    if pid:
        from planet_maiko.models.pupdate import Pupdate
        p = db.session.get(Pupdate, pid)
        if p is not None:
            title = title or (p.title or "")
            url = url or p.url
            if p.body:
                body_text = p.body.strip()
    create_memo(
        kind="flow_memo",
        category="info",
        title=title or "Flow notification",
        body=body_text[:4000] or "(no detail)",
        url=url,
        cta_label="Open" if url else None,
        priority=priority,
        source_pupdate_id=pid,
        extra={"workflow_run_id": run.id},
    )


def _action_complete_linked_task(run):
    """Close the review/coding tasks linked to the triggering pupdate's URL
    (cancel their jobs, dismiss sibling pupdates). Reuses the automation action
    so behavior matches the "Close linked task on PR approved/merged" rules
    exactly. No-op without a triggering pupdate."""
    pid = run.triggering_pupdate_id
    if not pid:
        return
    from planet_maiko.database import db
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.brain.automations.actions.pupdate import _act_complete_linked_task
    p = db.session.get(Pupdate, pid)
    if p is None:
        return
    # The automation action only reads `.id` off its first arg (for logging);
    # a tiny shim lets us reuse all its task/job/pupdate cleanup unchanged.
    shim = type("_FlowAuto", (), {"id": f"flow-run-{run.id}"})()
    _act_complete_linked_task(shim, {}, pupdate=p)


def _action_create_task(title, body, run):
    import uuid as _uuid
    from planet_maiko.database import db
    from planet_maiko.models.task import Task
    from planet_maiko.orchestration import maybe_spawn
    scope_repo = (run.extra or {}).get("scope_repo")
    role = "coding"
    profile = maybe_spawn(role, scope_repo)
    extra = {"description": (body or "").strip(), "source": "flow", "workflow_run_id": run.id}
    if scope_repo:
        extra["repo"] = scope_repo
    task = Task(
        id=f"flow-{_uuid.uuid4().hex[:10]}",
        title=title or "Flow task",
        type=role,
        status="new",
        priority="normal",
        assigned_agent_id=profile.id,
        tags=["flow"],
        extra=extra,
    )
    db.session.add(task)
    db.session.commit()


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
            # A loop edge is a graph-declared back-edge A->B (data.loop) with
            # an optional data.maxLoops cap. Forward edges drive readiness +
            # dataflow; loop edges drive the bounded back-loop (1.5) and must
            # NOT count toward a node's inbound, or the loop target would
            # deadlock waiting on the very node that loops back to it.
            loop_edges = [e for e in edges if (e.get("data") or {}).get("loop")]
            fwd_edges = [e for e in edges if not (e.get("data") or {}).get("loop")]
            # Trigger nodes are entry points, not data producers: a node fed
            # only by triggers is a root, seeded from run.extra.input (the
            # pupdate that fired the run). They start `done` (flows.start_run).
            trigger_ids = {n.get("id") for n in nodes if n.get("kind") == "trigger"}
            # Fire-and-forget action nodes (create memo/task) pass their input
            # through, so they're excluded from real_inbound the same way
            # triggers are. A "run a skill" action (subtype run_agent_job) is
            # NOT excluded: it's an awaited step that produces output, so a
            # node wired after it reads that output like any role node does.
            action_ids = {
                n.get("id") for n in nodes
                if n.get("kind") == "action"
                and (n.get("config") or {}).get("subtype") != "run_agent_job"
            }
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

            # 1.5 Loop edges (graph-defined). A loop edge is a back-edge
            #     A->B declared in the graph (data.loop, capped by
            #     data.maxLoops, default _MAX_REVIEW_ROUNDS). When the source
            #     node A finishes a pass and signals "another round" (a
            #     generic continue/stop bit via `maiko request-changes`, NOT
            #     an output type or role), the engine re-runs the target B
            #     with A's feedback and re-arms A, up to the cap. No signal
            #     and A settles so the run proceeds along A's forward edges.
            #     The topology (which B) and the cap come straight from the
            #     graph definition — never inferred from roles or outputs.
            for le in loop_edges:
                a_node, b_node = le.get("source"), le.get("target")
                if not a_node or not b_node:
                    continue
                try:
                    max_loops = int((le.get("data") or {}).get("maxLoops")
                                    or _MAX_REVIEW_ROUNDS)
                except (TypeError, ValueError):
                    max_loops = _MAX_REVIEW_ROUNDS
                for rv in nrs_by_node.get(a_node, []):
                    if rv.status != "done" or not rv.agent_job_id:
                        continue
                    if (rv.extra or {}).get("loop_settled"):
                        continue
                    rv_job = db.session.get(AgentJob, rv.agent_job_id)
                    feedback = _loop_signal(rv_job)
                    if not feedback:
                        rv.extra = {**(rv.extra or {}), "loop_settled": True}
                        continue
                    # Resolve WHICH INSTANCE of the loop target node to re-run.
                    # The NODE is fixed by the graph edge (b_node); only the
                    # instance is resolved here: a fanned A_i carries
                    # paired_to = its B_i NodeRun (set at propagation), a 1:1 A
                    # uses the single instance of b_node. The target needs a
                    # worktree to revise in.
                    target_nr = target_job = None
                    paired = (rv.extra or {}).get("paired_to")
                    if paired:
                        bnr = db.session.get(NodeRun, paired)
                        bj = (
                            db.session.get(AgentJob, bnr.agent_job_id)
                            if bnr and bnr.agent_job_id else None
                        )
                        if bnr and bnr.node_id == b_node and bj and bj.worktree_path:
                            target_nr, target_job = bnr, bj
                    if target_nr is None:
                        insts = [i for i in nrs_by_node.get(b_node, []) if i.agent_job_id]
                        if len(insts) == 1:
                            bnr = insts[0]
                            bj = db.session.get(AgentJob, bnr.agent_job_id)
                            if bj and bj.worktree_path:
                                target_nr, target_job = bnr, bj
                    round_n = (rv.extra or {}).get("round", 0)
                    if not target_job or round_n >= max_loops:
                        rv.extra = {
                            **(rv.extra or {}),
                            "loop_settled": True,
                            "loop_result": "max_rounds" if target_job else "no_target",
                        }
                        continue
                    # Hand the feedback back through the SAME inbox + resume
                    # path a human diff-review uses; the target revises on its
                    # branch and replies ready_for_review again.
                    db.session.add(AgentMessage(
                        task_id=target_job.id,
                        direction="to_agent",
                        sender=rv.agent_type or "loop",
                        content=_revision_message(feedback, rv_job),
                        message_type="review",
                    ))
                    db.session.commit()
                    if not _resume_agent_with_review(target_job.id, target_job.worktree_path):
                        rv.extra = {
                            **(rv.extra or {}),
                            "loop_settled": True,
                            "loop_result": "wake_failed",
                        }
                        continue
                    # Target iterates again on its branch; A re-arms to re-run
                    # after it (paired_to preserved so it re-targets the same
                    # instance next round).
                    target_job.status = "running"
                    target_nr.status = "running"
                    target_nr.extra = {**(target_nr.extra or {}), "round": round_n + 1}
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

            def _spawn_job(role, description, node_id, title=None, repo=None,
                           parent_branch=None, parent_base_sha=None):
                """Mint one queued AgentJob for a node (or a scatter
                instance). Lazy-spawns the (role, scope) profile so
                execute_jobs resolves the right role, including custom ones
                like planner/decomposer whose kind isn't in the builtin map.
                `title`/`repo` come from the producing output when present (a
                scattered task names + optionally re-homes its coder); repo
                falls back to the run's scope_repo, the shared default.
                `parent_branch`/`parent_base_sha` stack a dependent task on its
                parent: execute_jobs cuts the worktree from origin/<parent_branch>
                so the child builds on the parent's work, not from scratch."""
                job_repo = repo or scope_repo
                profile = maybe_spawn(role, job_repo)
                extra = {"workflow_run_id": run.id, "node_id": node_id}
                if parent_branch:
                    extra["parent_branch"] = parent_branch
                if parent_base_sha:
                    extra["parent_base_sha"] = parent_base_sha
                job = AgentJob(
                    id=uuid.uuid4().hex[:24],
                    kind=role,
                    title=title or f"{role} step",
                    description=description,
                    scope_repo=job_repo,
                    created_by="system",
                    agent_profile_id=profile.id,
                    status="queued",
                    extra=extra,
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
                inbound = [e.get("source") for e in fwd_edges if e.get("target") == nid]

                # DEFERRED STACKED CHILDREN: this node was already scattered on
                #   an earlier tick (instances carry scatter_origin) and some are
                #   pending on a parent task. Spawn each whose parent has finished
                #   and pushed its branch, cutting the child off that branch, and
                #   leave the rest pending. This intercepts before the scatter
                #   detection below so the node never re-scatters.
                if any((i.extra or {}).get("scatter_origin") for i in insts):
                    by_item = {
                        (i.extra or {}).get("item_id"): i
                        for i in insts if (i.extra or {}).get("item_id")
                    }
                    for child in pendings:
                        cx = child.extra or {}
                        dep = cx.get("depends_on")
                        if not dep:
                            # Independent instance still pending (unusual): spawn
                            # off the run's base.
                            job = _spawn_job(role, cx.get("content"), nid,
                                             title=cx.get("label"), repo=cx.get("repo"))
                            child.status = "queued"
                            child.agent_job_id = job.id
                            advanced += 1
                            continue
                        parent = by_item.get(dep)
                        if parent is None:
                            logger.warning(
                                f"[cycle] stacked task {cx.get('item_id')!r} depends "
                                f"on unknown {dep!r}; starting from the base branch"
                            )
                            job = _spawn_job(role, cx.get("content"), nid,
                                             title=cx.get("label"), repo=cx.get("repo"))
                            child.status = "queued"
                            child.agent_job_id = job.id
                            advanced += 1
                            continue
                        if parent.status in ("failed", "skipped"):
                            child.status = "skipped"
                            child.error = "the task it stacks on did not finish"
                            continue
                        if parent.status != "done" or not parent.agent_job_id:
                            continue  # parent still working; wait a tick
                        parent_job = db.session.get(AgentJob, parent.agent_job_id)
                        if not parent_job or not parent_job.branch or not parent_job.worktree_path:
                            continue  # parent has no branch yet; wait
                        # Push the parent's branch so the child can cut from
                        # origin/<branch>; retry next tick if the push fails.
                        if not _push_branch(parent_job.worktree_path, parent_job.branch):
                            continue
                        parent_sha = None
                        try:
                            r = _git(["rev-parse", "HEAD"], parent_job.worktree_path)
                            if r.returncode == 0:
                                parent_sha = (r.stdout or "").strip() or None
                        except Exception:
                            pass
                        job = _spawn_job(
                            role, cx.get("content"), nid,
                            title=cx.get("label"), repo=cx.get("repo"),
                            parent_branch=parent_job.branch, parent_base_sha=parent_sha,
                        )
                        child.status = "queued"
                        child.agent_job_id = job.id
                        child.extra = {**cx, "parent_branch": parent_job.branch}
                        advanced += 1
                    continue

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
                    _emit_gate_memo(run, nid, placeholder, fwd_edges, nrs_by_node)
                    continue

                # Action node. "Run a skill" (subtype run_agent_job) behaves
                # like a role node: compose its prompt, spawn the skill job,
                # link it to this step and await it, so its output flows
                # downstream. create memo/task stay fire-and-forget side-effects
                # run inline.
                if node.get("kind") == "action":
                    a_cfg = node.get("config") or {}
                    if (a_cfg.get("subtype") or "").strip() == "run_agent_job":
                        # Prompt = the skill's own focus (config.input) plus the
                        # seed (root) or the upstream artifacts / pushed branch.
                        a_blocks = []
                        skill_focus = (a_cfg.get("input") or "").strip()
                        if skill_focus:
                            a_blocks.append(skill_focus)
                        real_in = [s for s in inbound if s not in trigger_ids and s not in action_ids]
                        a_push_failed = False
                        if not real_in:
                            # Root step: seed from the flow input (a pupdate
                            # body is real context; a schedule's seed is just a
                            # placeholder, but harmless next to the focus).
                            seed = (run.extra or {}).get("input")
                            if seed and seed != skill_focus:
                                a_blocks.append(seed)
                        else:
                            for src in inbound:
                                for src_nr in nrs_by_node.get(src, []):
                                    if src_nr.status != "done" or not src_nr.agent_job_id:
                                        continue
                                    src_job = db.session.get(AgentJob, src_nr.agent_job_id)
                                    if not src_job:
                                        continue
                                    block, pf = _compose_block(src_nr, src_job)
                                    if pf:
                                        a_push_failed = True
                                        break
                                    if block:
                                        a_blocks.append(block)
                                if a_push_failed:
                                    break
                        if a_push_failed:
                            placeholder.status = "failed"
                            placeholder.error = "couldn't push the upstream branch for the skill to read"
                            continue
                        job = _spawn_run_agent_job(
                            a_cfg, "\n\n".join(a_blocks) or None, run, nid,
                        )
                        placeholder.status = "queued"
                        placeholder.agent_job_id = job.id
                        advanced += 1
                        continue
                    # Fire-and-forget side-effect (create a memo / task): run
                    # inline with its input (the upstream artifact, or the
                    # pupdate that fired a trigger), then mark it done.
                    real_in = [s for s in inbound if s not in trigger_ids and s not in action_ids]
                    action_input = None
                    for src in real_in:
                        for s_nr in nrs_by_node.get(src, []):
                            if s_nr.status == "done" and s_nr.agent_job_id:
                                sj = db.session.get(AgentJob, s_nr.agent_job_id)
                                if sj and sj.artifact:
                                    action_input = sj.artifact
                                    break
                        if action_input:
                            break
                    if action_input is None:
                        action_input = (run.extra or {}).get("input")
                    _run_action_node(node, action_input, run)
                    placeholder.status = "done"
                    continue

                # Gather every done upstream instance + its job.
                upstream = []
                for src in inbound:
                    for src_nr in nrs_by_node.get(src, []):
                        if src_nr.status != "done" or not src_nr.agent_job_id:
                            continue
                        src_job = db.session.get(AgentJob, src_nr.agent_job_id)
                        if not src_job:
                            continue
                        upstream.append((src_nr, src_job))

                # --- Scatter: an upstream that emitted MORE THAN ONE output
                #     this node accepts fans out, one instance per output. The
                #     trigger is the data (N matching outputs), not the
                #     producer's role — a decomposer emitting N `task`s is just
                #     the common case. Falls back to parsing TASK: blocks for a
                #     legacy tasks-producer that emitted no structured outputs.
                #     Reuse the placeholder as instance 0, mint NodeRuns for the
                #     rest; each instance gets its own item as the prompt. ---
                this_at = get_agent_type(role)
                accepts = (this_at.accepts if this_at else None) or []
                scatter_items = None
                for _src_nr, src_job in upstream:
                    matched = _matching_outputs(src_job, accepts)
                    if len(matched) > 1:
                        scatter_items = matched
                        break
                    st = get_agent_type(src_job.kind)
                    if st and st.output_kind == "tasks":
                        legacy = _parse_tasks(src_job.artifact)
                        if len(legacy) > 1:
                            scatter_items = [{"content": t} for t in legacy]
                            break
                if scatter_items is not None:
                    # Mint a NodeRun per item carrying its payload + stacking
                    # metadata (item_id, depends_on). Independent items spawn now
                    # off the run's base; dependent items stay pending and the
                    # deferred-stacked-children branch above spawns each off its
                    # parent's branch once the parent finishes.
                    for idx, item in enumerate(scatter_items):
                        content = item.get("content")
                        # The output's title names the spawned job (else the
                        # task's first line); its repo re-homes that one coder
                        # (else the run's repo, applied in _spawn_job).
                        label = item.get("title") or _first_line(content)
                        item_id = (item.get("id") or "").strip() or f"task{idx}"
                        dep = item.get("depends_on")
                        if isinstance(dep, list):
                            dep = dep[0] if dep else None  # P1: a single parent
                        if isinstance(dep, str):
                            dep = dep.strip() or None
                        target = placeholder if idx == 0 else NodeRun(
                            workflow_run_id=run.id, node_id=nid, agent_type=role,
                        )
                        if idx > 0:
                            db.session.add(target)
                        target.extra = {
                            "instance": idx, "label": label,
                            "scatter_origin": True, "item_id": item_id,
                            "depends_on": dep,
                            "content": content, "repo": item.get("repo"),
                        }
                        if dep:
                            target.status = "pending"  # wait for the parent task
                        else:
                            job = _spawn_job(
                                role, content, nid,
                                title=label, repo=item.get("repo"),
                            )
                            target.status = "queued"
                            target.agent_job_id = job.id
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
                # A trigger is the run's entry, not a producer, so a node fed
                # only by triggers is a root and seeds from run.extra.input.
                real_inbound = [s for s in inbound if s not in trigger_ids and s not in action_ids]
                blocks = []
                push_failed = False
                if not real_inbound:
                    # Root node: seed it with the kickoff input (the flow's
                    # "Task (input)", or the pupdate that fired a trigger).
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
