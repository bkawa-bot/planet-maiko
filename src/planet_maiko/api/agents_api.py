import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from flask import Blueprint, current_app, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.agents.brain_session import run_skill, get_status as brain_status, ONE_SHOT_ROLE_FOR_TYPE
from planet_maiko.agents.runtime import prepare, list_prepared, cleanup
from planet_maiko.agents.monitor import get_agent_activity, get_queued_agent_tasks, process_agent_pupdates, get_stuck_agents, get_recoverable_agents
from planet_maiko.agents.sessions import _get_sessions, _save_sessions, _set_session
from planet_maiko.agents.skills import list_skills
from planet_maiko.agents.terminal import _find_claude_session_file, _launch_terminal

logger = logging.getLogger(__name__)

agents_bp = Blueprint("agents", __name__)


@agents_bp.route("/brain/session", methods=["GET"])
def get_brain_session():
    """Get brain session status (runtime info)."""
    return jsonify(brain_status())


@agents_bp.route("/skills", methods=["GET"])
def get_skills():
    """List all available skills."""
    return jsonify(list_skills())


@agents_bp.route("/skills/<skill_id>", methods=["GET"])
def get_skill_detail(skill_id):
    """Get a skill's full details including prompt."""
    from planet_maiko.models.custom_skill import CustomSkill
    skill = db.get_or_404(CustomSkill, skill_id)
    if skill.deleted_at is not None:
        return jsonify({"error": "Skill not found"}), 404
    return jsonify(skill.to_dict())


@agents_bp.route("/skills", methods=["POST"])
def create_skill():
    """Create a custom skill."""
    from planet_maiko.models.custom_skill import CustomSkill
    data = request.get_json()
    skill = CustomSkill(
        id=data["id"],
        name=data["name"],
        description=data.get("description", ""),
        prompt=data["prompt"],
        mcps=data.get("mcps", []),
        icon=data.get("icon", "wand"),
        is_default=False,
        needs_worktree=bool(data.get("needs_worktree", False)),
    )
    db.session.add(skill)
    db.session.commit()
    return jsonify(skill.to_dict()), 201


@agents_bp.route("/skills/<skill_id>", methods=["PATCH"])
def update_skill(skill_id):
    """Update a skill's prompt, name, description, or MCPs."""
    from planet_maiko.models.custom_skill import CustomSkill
    skill = db.get_or_404(CustomSkill, skill_id)
    data = request.get_json()
    if "name" in data:
        skill.name = data["name"]
    if "description" in data:
        skill.description = data["description"]
    if "prompt" in data:
        skill.prompt = data["prompt"]
        skill.user_edited = True
    if "mcps" in data:
        skill.mcps = data["mcps"]
    if "icon" in data:
        skill.icon = data["icon"]
    if "needs_worktree" in data:
        skill.needs_worktree = bool(data["needs_worktree"])
    db.session.commit()
    return jsonify(skill.to_dict())


@agents_bp.route("/skills/<skill_id>", methods=["DELETE"])
def delete_skill(skill_id):
    """Delete a skill.

    User-created skills are hard-deleted. Defaults are soft-deleted
    (deleted_at = now) so the seed pass on next boot doesn't re-create
    them — the row sticks around as a tombstone the seed can see and
    skip. List/get queries filter out tombstones, so the user-visible
    behavior is identical either way.
    """
    from datetime import datetime, timezone
    from planet_maiko.models.custom_skill import CustomSkill
    skill = db.get_or_404(CustomSkill, skill_id)
    if skill.is_default:
        skill.deleted_at = datetime.now(timezone.utc)
    else:
        db.session.delete(skill)
    db.session.commit()
    return jsonify({"status": "deleted"})


@agents_bp.route("/agents/assign", methods=["POST"])
def assign_agent():
    """Assign an agent to a task by enqueuing an AgentJob.

    Single unified path for every role (coding / review / investigation
    / cartographer): create an AgentJob with status="queued" and let
    the brain cycle's _phase_execute_agent_jobs handle the worktree
    prep + headless kickoff. Same entry point, same audit trail, same
    Active Agents UI for every kind of work.

    Inputs (JSON body):
        task_id, profile_id   — required
        repo_path             — required for coding (user-picked clone);
                                resolved from scope for the others
        plan_first            — coding only; persisted on job.extra
        branch_name           — optional branch prefix override
        custom_prompt         — optional one-off intent override
        specialty_id          — optional specialty to layer onto the run
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE
    from planet_maiko.orchestration import resolve_repo_path, scope_for_task
    import uuid as _uuid

    data = request.get_json()
    task_id = data.get("task_id")
    profile_id = data.get("profile_id")
    repo_path = data.get("repo_path", "")
    plan_first = bool(data.get("plan_first", False))
    branch_name = (data.get("branch_name") or "").strip() or None
    custom_prompt = data.get("custom_prompt", "")
    specialty_id = (data.get("specialty_id") or "").strip() or None

    if not task_id or not profile_id:
        return jsonify({"error": "task_id and profile_id are required"}), 400

    task = db.get_or_404(Task, task_id)
    profile = db.get_or_404(AgentProfile, profile_id)
    role = profile.role or "coding"
    if specialty_id and specialty_id not in (profile.specialty_ids or []):
        specialty_id = None  # silently drop — agent doesn't have it attached

    # Resolve scope_repo + local_path. Coding lets the user pick any
    # clone via repo_path; review / investigation / cartographer derive
    # the path from the task's scope. We need a local clone before
    # queueing — surfacing "no clone" at queue time keeps the failure
    # legible (vs. discovering it inside the cycle a tick later).
    if role == "coding":
        if not repo_path:
            return jsonify({"error": "repo_path is required. Select a repo in the assign modal."}), 400
        if not os.path.isdir(repo_path):
            return jsonify({"error": f"Repository path not found: {repo_path}"}), 400
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            return jsonify({"error": f"Not a git repository: {repo_path}"}), 400
        scope_repo = scope_for_task(task)  # may be None for coding
    else:
        scope_repo = scope_for_task(task)
        local_path = resolve_repo_path(scope_repo)
        if not local_path:
            if not scope_repo:
                return jsonify({
                    "error": (
                        "This task has no repo scope set. Edit the task and "
                        "give it a scope_repo so the agent knows which repo "
                        "to work in."
                    ),
                }), 400
            return jsonify({
                "error": (
                    f"No local clone of {scope_repo} found. Add the parent "
                    "directory where your clones live under Settings > Plugins "
                    "> github > Repo roots."
                ),
            }), 400
        repo_path = local_path
        # Normalize task.type so monitor + cycle phases recognize the
        # one-shot kinds. Cartographer task type is "cartograph" by
        # convention; everything else maps 1:1 with the role.
        type_map = {"investigation": "investigation", "cartographer": "cartograph", "review": "review"}
        if task.type not in ONE_SHOT_ROLE_FOR_TYPE and task.type not in ("review", "pr_review"):
            task.type = type_map.get(role, task.type)

    task.assigned_agent_id = profile.id
    if task.status == "new":
        task.status = "in_progress"

    # Reuse a queued / pending_approval job if one exists for this task
    # (e.g. user clicked Assign twice, or the spawn-jobs phase beat us
    # to it). Otherwise mint a fresh one.
    job_extra = {
        "repo_path": repo_path,
        "branch_prefix": branch_name,
        "plan_first": plan_first if role == "coding" else False,
        "custom_prompt": custom_prompt,
        "specialty_id": specialty_id,
    }
    # Drop None / empty values so we don't litter the row with junk.
    job_extra = {k: v for k, v in job_extra.items() if v}

    existing = AgentJob.query.filter_by(source_task_id=task.id).first()
    if existing and existing.status in ("queued", "pending_approval", "failed"):
        job = existing
        job.agent_profile_id = profile.id
        job.status = "queued"
        job.error = None
        job.finished_at = None
        merged_extra = dict(job.extra or {})
        merged_extra.update(job_extra)
        job.extra = merged_extra
    else:
        job = AgentJob(
            id=f"job-{_uuid.uuid4().hex[:10]}",
            kind=task.type,
            title=task.title,
            description=(task.extra or {}).get("description") or (task.extra or {}).get("body"),
            scope_repo=scope_repo,
            priority=task.priority or "normal",
            created_by="user",
            source_task_id=task.id,
            agent_profile_id=profile.id,
            requires_approval=False,
            approved_at=datetime.now(timezone.utc),
            approved_by="user",
            status="queued",
            extra=job_extra,
        )
        db.session.add(job)

    # Stamp agent_job_id onto task.extra so the diff / relaunch / report
    # UIs can find the job before the cycle has run. worktree_path /
    # branch get filled in by execute_jobs once prepare succeeds.
    db.session.flush()
    task_extra = dict(task.extra or {})
    task_extra["agent_job_id"] = job.id
    if specialty_id:
        task_extra["specialty_id"] = specialty_id
    task.extra = task_extra

    db.session.commit()

    return jsonify({
        "task": task.to_dict(),
        "agent": profile.to_dict(),
        "job": job.to_dict(),
        "mode": role,
    }), 201


# _build_task_prompt moved to planet_maiko.orchestration.build_task_prompt
# so the pack dispatcher + brain cycle can use the same composer.


@agents_bp.route("/agents/resume-session", methods=["POST"])
def resume_session():
    """Attach to a running agent session for live viewing.

    Priority:
    1. tmux attach (if agent is running in a tmux session — best experience)
    2. Tail Claude's session JSONL file with jq pretty-printing (live read-only view)
    3. Open terminal in worktree (last-resort fallback)
    """
    import subprocess
    import sys
    import shutil

    data = request.get_json()
    job_id = data.get("job_id") or data.get("task_id", "")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400

    session_info = _get_sessions().get(job_id)
    if not session_info:
        from planet_maiko.models.agent_job import AgentJob
        job = db.session.get(AgentJob, job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        if not job.session_id:
            return jsonify({"error": "No session found. Launch the agent first."}), 404
        session_info = {
            "session_id": job.session_id,
            "working_path": job.worktree_path or "",
        }

    session_id = session_info["session_id"]
    working_path = session_info.get("working_path", "")

    tmux_path = shutil.which("tmux")
    session_name = f"maiko-{job_id}"
    has_tmux = False
    if tmux_path:
        result = subprocess.run(
            [tmux_path, "has-session", "-t", session_name],
            capture_output=True,
        )
        has_tmux = result.returncode == 0

    # If the wake orchestrator is currently running claude for this
    # task, spawning a second `claude --resume` on the same session_id
    # would race on the JSONL file. Downgrade to a read-only tail so
    # the user can watch what the agent is doing live, without
    # corrupting the session.
    from planet_maiko.agents.wake import is_working
    agent_busy = is_working(job_id)

    mode = None
    if has_tmux:
        cmd = f"tmux attach -t {session_name}"
        mode = "tmux"
    elif agent_busy:
        session_file = _find_claude_session_file(working_path, session_id)
        if session_file:
            cmd = (
                f"echo 'Agent is currently working — read-only view.' && "
                f"echo 'Close anytime; the agent keeps running in the background.' && "
                f"echo '' && tail -f {session_file}"
            )
            mode = "tail-busy"
        else:
            return jsonify({
                "error": "Agent is working but session file isn't on disk yet.",
                "busy": True,
            }), 409
    elif working_path and shutil.which("claude"):
        # For autonomous review/investigation agents (and any coding
        # agent launched without tmux), `claude --resume <id>` opens
        # an interactive session restored to the point the background
        # run reached. --dangerously-skip-permissions matches the
        # original headless run so MCP tools (maiko-channel reply,
        # check_inbox, etc.) don't get permission-prompted mid-resume
        # and stall the conversation. Worktree isolation already
        # bounds blast radius.
        #
        # Without an initial prompt, claude --resume drops into an
        # idle interactive prompt — the agent stops working as soon as
        # the user opens View Session, which is exactly the opposite
        # of what View Session implies. Append a "keep going" nudge so
        # the agent picks back up. Plain ASCII, no inner quotes (the
        # cmd string gets interpolated through shells / AppleScript /
        # cmd.exe on different platforms; quotes break escaping).
        resume_prompt = (
            "Resuming via View Session. Check your inbox for any new "
            "messages from the user, give a brief one-line status of "
            "where you left off, and continue working on the task."
        )
        cmd = (
            f'cd {working_path} && '
            f'claude --resume {session_id} --dangerously-skip-permissions '
            f'"{resume_prompt}"'
        )
        mode = "resume"
    else:
        session_file = _find_claude_session_file(working_path, session_id)
        if session_file:
            cmd = f"echo 'Tailing agent session ({session_id})' && echo '' && tail -f {session_file}"
            mode = "tail-raw"
        elif working_path and os.path.isdir(working_path):
            cmd = f"cd {working_path} && echo 'No session file yet. Worktree:' && pwd && exec $SHELL"
            mode = "worktree"
        else:
            return jsonify({"error": "No session file or worktree found."}), 404

    try:
        _launch_terminal(cmd)
        return jsonify({
            "status": "opened",
            "session_id": session_id,
            "mode": mode,
            "working_path": working_path,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/agents/open-terminal", methods=["POST"])
def open_terminal():
    """Open a terminal — attach to tmux session if running, or start fresh."""
    import subprocess
    import sys
    import shutil
    import uuid

    data = request.get_json()
    path = data.get("path", "")
    task_id = data.get("task_id", "")
    branch = data.get("branch", "")

    if not path or not os.path.isdir(path):
        return jsonify({"error": "Invalid path"}), 400

    tmux_path = shutil.which("tmux")
    session_name = f"maiko-{task_id}" if task_id else ""

    # Check if tmux session exists
    has_tmux_session = False
    if tmux_path and session_name:
        result = subprocess.run(
            [tmux_path, "has-session", "-t", session_name],
            capture_output=True,
        )
        has_tmux_session = result.returncode == 0

    if has_tmux_session:
        attach_cmd = f"tmux attach -t {session_name}"
    else:
        try:
            from planet_maiko.config import load_config
            allowed_tools = list(load_config().get("brain", {}).get("allowed_tools", []))
        except Exception:
            allowed_tools = []
        tools_flags = " ".join(f'--allowedTools "{t}"' for t in allowed_tools)
        checkout = f"git checkout {branch} && " if branch else ""
        initial_prompt = "Read TASK.md and CLAUDE.md in this directory. Begin working on the task following the protocol."

        # Generate a session ID upfront so we can resume later
        session_id = str(uuid.uuid4())
        if task_id:
            _set_session(task_id, session_id, path)

        attach_cmd = f'{checkout}cd {path} && claude --session-id {session_id} {tools_flags} "{initial_prompt}"'

    try:
        _launch_terminal(attach_cmd)
        info = _get_sessions().get(task_id, {})
        return jsonify({"status": "opened", "path": path, "tmux_attached": has_tmux_session, "session_id": info.get("session_id") if isinstance(info, dict) else info})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/skills/<skill_name>/run", methods=["POST"])
def run_skill_endpoint(skill_name):
    """Invoke a specialty by creating a Task + AgentJob.

    Two execution shapes:
      - Specialty with needs_worktree=False: runs inline in this
        request and returns the output so the caller can render it.
      - Specialty with needs_worktree=True: queues an AgentJob; the
        cycle's execute phase picks it up on the next tick. The
        response is 202 with task_id/job_id so the client can poll
        its memo / follow-up.

    Output lands as a skill_result Memo attributed to the lazy-
    spawned specialty agent. Visible on Home's Recent Skills and
    attributed to the agent on the Pack page.

    Falls through to a direct-run path for anything not registered as
    a CustomSkill (internal engine calls like home-overview / scene
    that route through this endpoint by name).
    """
    from planet_maiko.models.custom_skill import CustomSkill
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.orchestration import route, is_ready, maybe_spawn
    from planet_maiko.brain.cycle import _execute_lightweight_specialty
    import uuid as _uuid

    specialty = db.session.get(CustomSkill, skill_name)
    data = request.get_json() or {}

    # Direct-run path for unregistered skills (home-overview and
    # other engine-internal prompts called by name). Keeps tests +
    # internal callers working.
    if specialty is None:
        result = run_skill(
            skill_name,
            context=data.get("context", {}),
            working_dir=data.get("working_dir"),
        )
        return jsonify(result)

    scope_repo = (
        (data.get("context") or {}).get("repo")
        or (data.get("context") or {}).get("repository")
        or None
    )

    task_id = f"task-{_uuid.uuid4().hex[:10]}"
    task = Task(
        id=task_id,
        title=data.get("title") or specialty.name,
        type=specialty.id,
        priority=data.get("priority") or "normal",
        status="new",
        extra={
            "description": data.get("description") or specialty.description or "",
            "repo": scope_repo or "",
            "from_run_now": True,
        },
        tags=["specialty-run"],
    )
    db.session.add(task)
    db.session.flush()
    route(task)
    if not is_ready(task):
        task.status = "blocked"

    profile = maybe_spawn(specialty.id, scope_repo)
    task.assigned_agent_id = profile.id

    job = AgentJob(
        id=_uuid.uuid4().hex[:24],
        kind=specialty.id,
        title=task.title,
        description=(task.extra or {}).get("description"),
        scope_repo=scope_repo,
        priority=task.priority,
        created_by="user",
        source_task_id=task.id,
        agent_profile_id=profile.id,
        status="queued",
        extra={},
    )
    db.session.add(job)
    db.session.commit()

    if not specialty.needs_worktree:
        ok = _execute_lightweight_specialty(job, specialty)
        return jsonify({
            "success": ok and job.status == "done",
            "task_id": task.id,
            "job_id": job.id,
            "status": job.status,
            "output": job.artifact or "",
            "error": job.error if job.status == "failed" else None,
        })

    return jsonify({
        "success": True,
        "task_id": task.id,
        "job_id": job.id,
        "status": "queued",
    }), 202


@agents_bp.route("/agents", methods=["GET"])
def get_agents():
    """List all prepared agent worktrees."""
    return jsonify(list_prepared())


@agents_bp.route("/agents/activity", methods=["GET"])
def get_activity():
    """Get recent agent activity (pupdates from agents)."""
    return jsonify(get_agent_activity())


@agents_bp.route("/agents/recoverable", methods=["GET"])
def get_recoverable():
    """Cancelled tasks + jobs whose worktree is still on disk —
    revivable. The active page surfaces these in a 'Recently stopped'
    section so a misclick doesn't cost a day of context."""
    return jsonify(get_recoverable_agents())


@agents_bp.route("/agents/worktrees/stats", methods=["GET"])
def get_worktree_stats():
    """Snapshot of every Planet-Maiko-managed worktree on disk.

    Used by the Settings → Worktree maintenance section to show what's
    accumulating (count, total bytes, oldest mtime). Cheap-ish — walks
    each configured repo's .maiko-worktrees plus the scratch root,
    sizing each dir.
    """
    from planet_maiko.agents.runtime import worktree_stats
    return jsonify(worktree_stats())


@agents_bp.route("/agents/worktrees/sweep", methods=["POST"])
def post_worktree_sweep():
    """Manually trigger a worktree sweep.

    Body: { "max_age_days": int }  (default 14)

    Removes worktrees older than max_age_days whose AgentJob is
    terminal (done / cancelled / failed). Never touches active jobs.
    Returns a stats dict describing what happened.
    """
    from planet_maiko.agents.runtime import sweep_old_worktrees
    data = request.get_json(silent=True) or {}
    try:
        max_age_days = int(data.get("max_age_days", 14))
    except (TypeError, ValueError):
        return jsonify({"error": "max_age_days must be an integer"}), 400
    result = sweep_old_worktrees(max_age_days)
    return jsonify(result)


@agents_bp.route("/agents/queued", methods=["GET"])
def get_queued():
    """Get tasks with an assigned agent but no worktree/activity yet.

    These are review/investigation/coding tasks that have been routed
    by the brain cycle but haven't yet been prepared and run. Surfaced
    on the Agents tab so users can see "yes, the agent is queued and
    will start on the next cycle" instead of staring at an empty page.
    """
    return jsonify(get_queued_agent_tasks())


@agents_bp.route("/agents/stuck", methods=["GET"])
def get_stuck():
    """Get agents that haven't reported in a while."""
    return jsonify(get_stuck_agents())


@agents_bp.route("/agents/process", methods=["POST"])
def process_agents():
    """Process agent pupdates (auto-complete tasks, etc.)."""
    result = process_agent_pupdates()
    return jsonify(result)


@agents_bp.route("/agents/prepare", methods=["POST"])
def prepare_agent():
    """Prepare a worktree for an agent task.

    Sets up the worktree with TASK.md and CLAUDE.md and returns the
    launch metadata. The agent is not started here — callers invoke
    the headless kickoff separately when ready.
    """
    data = request.get_json()
    result = prepare(
        task_id=data["task_id"],
        task_title=data["task_title"],
        prompt=data["prompt"],
        repo_path=data["repo_path"],
        branch_prefix=data.get("branch_prefix", "maiko"),
    )
    if not result:
        return jsonify({"error": "Failed to prepare agent worktree"}), 500
    return jsonify(result), 201


@agents_bp.route("/agents/cleanup", methods=["POST"])
def cleanup_agent():
    """Clean up a worktree after an agent is done."""
    data = request.get_json()
    cleanup(data["repo_path"], data["branch"])
    return jsonify({"status": "cleaned up"})


# --- Agent Inbox (bidirectional communication) ---

@agents_bp.route("/agents/<job_id>/inbox", methods=["GET"])
def get_agent_inbox(job_id):
    """Get messages for an agent (channel polls this every ~15s).

    Query params:
        unread_only: "true" to only return unread messages (default: true)
        mark_read: "true" to auto-mark returned messages as read (default: true)
    """
    unread_only = request.args.get("unread_only", "true").lower() == "true"
    mark_read = request.args.get("mark_read", "true").lower() == "true"

    query = AgentMessage.query.filter_by(task_id=job_id, direction="to_agent")
    if unread_only:
        # Quick count check to avoid loading objects when nothing is unread
        count = query.filter_by(read=False).count()
        if count == 0:
            return jsonify([])
        query = query.filter_by(read=False)

    messages = query.order_by(AgentMessage.created_at.asc()).all()

    if mark_read and messages:
        for m in messages:
            m.read = True
        db.session.commit()

    return jsonify([m.to_dict() for m in messages])


@agents_bp.route("/agents/<job_id>/inbox", methods=["POST"])
def send_to_agent(job_id):
    """Send a message to an agent (from dashboard, brain, or user).

    When sender is "user", also auto-wake the agent so the message
    actually gets read, otherwise it sits in the inbox until the next
    external trigger. Other senders (system, brain) are wake-free
    because they're usually paired with their own triggers.
    """
    data = request.get_json()
    msg = AgentMessage(
        task_id=job_id,
        direction="to_agent",
        sender=data.get("sender", "user"),
        content=data["content"],
        message_type=data.get("message_type", "message"),
    )
    db.session.add(msg)
    db.session.commit()

    woke_mode = "none"
    if msg.sender == "user":
        from planet_maiko.agents.wake import wake_agent
        chat_prompt = (
            "The user sent you a message. Call check_inbox to read it, "
            "then reply with your answer using recipient='user' so it "
            "surfaces in their inbox as a memo. They often ask and walk "
            "away, so an in-thread-only reply gets buried. Use the default "
            "message_type (omit it, or 'message'); status is chatter and "
            "won't generate a memo. After replying, continue working."
        )
        _ok, woke_mode = wake_agent(
            job_id, chat_prompt, source="chat",
        )

    out = msg.to_dict()
    out["wake_mode"] = woke_mode
    return jsonify(out), 201


@agents_bp.route("/agents/<job_id>/rerun", methods=["POST"])
def rerun_agent(job_id):
    """Re-fire the autonomous run for a job whose original kickoff
    silently died (claude crashed, MCP failed to load, network blip)
    and the agent never sent its first pupdate.

    Re-queues the AgentJob in place. Worktree + session_id are preserved
    so "View Session" stays valid.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_job import AgentJob

    job = db.session.get(AgentJob, job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404

    job.status = "queued"
    job.error = None
    job.started_at = None
    job.finished_at = None
    task = db.session.get(Task, job.source_task_id) if job.source_task_id else None
    if task and task.status == "in_progress":
        task.status = "new"
    db.session.commit()

    return jsonify({
        "status": "rerunning",
        "job_id": job.id,
        "task_id": task.id if task else None,
        "working_path": job.worktree_path,
    }), 202


# Outbox dispatcher — per-message-type handlers live in
# planet_maiko.api.agent_outbox so this stays a thin route.
@agents_bp.route("/agents/<task_id>/outbox", methods=["POST"])
def agent_sends_message(task_id):
    """Agent sends a message back (alternative to pupdate-based reporting).

    The url param is named `task_id` but the incoming id may be an
    AgentJob id (for cartograph / investigation / review runs) or a
    Task id (coding tasks + anything else Task-driven). We check
    AgentJob first, and if it resolves, route through the job-aware
    handler. Otherwise fall through to the Task path.
    """
    from planet_maiko.api.agent_outbox import (
        emit_user_facing_signal,
        handle_agent_job_reply,
        handle_pr_opened,
        handle_session_feedback,
        handle_task_insight,
        handle_task_ready_for_review,
    )
    from planet_maiko.models.agent_job import AgentJob as _AgentJob
    from planet_maiko.models.task import Task as _Task

    data = request.get_json()
    # recipient defaults to None (in-thread chatter). When the agent
    # explicitly sets recipient="user", emit_user_memo below mints a
    # Memo so the message reaches the inbox instead of only living
    # inside the task's chat thread.
    recipient = (data.get("recipient") or "").strip().lower() or None
    msg = AgentMessage(
        task_id=task_id,
        direction="from_agent",
        sender=data.get("sender", "agent"),
        recipient=recipient,
        content=data["content"],
        message_type=data.get("message_type", "message"),
    )
    db.session.add(msg)
    message_type = data.get("message_type", "message")

    # AgentJob path first — claim the reply if this id belongs to a job.
    job = None
    try:
        job = db.session.get(_AgentJob, task_id)
    except Exception:
        job = None
    if job is not None:
        handle_agent_job_reply(job, msg, data, message_type)
        if recipient == "user":
            _emit_user_memo(msg, job=job, task=None)
        db.session.commit()
        return jsonify({"ok": True, "message_id": msg.id, "target": "agent_job"})

    # Task path — order matters: ready_for_review parses the artifact +
    # sets verdict before the user-facing signal fires, so the memo's
    # cta_label can read the right field.
    task = db.session.get(_Task, task_id)
    if message_type == "ready_for_review":
        handle_task_ready_for_review(task_id, task, data)
    if message_type == "insight":
        handle_task_insight(task_id, task, msg, data)
    emit_user_facing_signal(task_id, task, msg, data, message_type)
    if message_type == "pr_opened":
        handle_pr_opened(task_id, data)
    if message_type == "feedback":
        handle_session_feedback(task_id, msg, data, _get_repo_for_task)
    if recipient == "user":
        _emit_user_memo(msg, job=None, task=task)

    db.session.commit()
    return jsonify(msg.to_dict()), 201


def _emit_user_memo(msg, *, job, task):
    """Surface an agent message that was explicitly addressed to the
    user as a Memo in the inbox. Without this, recipient="user"
    messages would only show up if the user happened to open the
    chat thread for that task / job — defeating the whole point of
    the agent flagging "you should see this."

    The memo's source_task_id keys off the linked Task (when the
    reply landed via a Task-driven flow) or the AgentJob's
    source_task_id; falls back to the message's task_id (which is
    the AgentJob id) so the user can still click-through to the chat.

    Logs at INFO so the boot trail records every user-targeted
    message that hits this path — makes "the message exists but
    no memo" easy to diagnose next time.
    """
    from planet_maiko.brain.memos import create_memo
    from planet_maiko.models.agent_profile import AgentProfile

    agent_profile_id = None
    source_task_id = None
    title = "Message from an agent"
    if job is not None:
        agent_profile_id = job.agent_profile_id
        source_task_id = job.source_task_id or job.id
        title = f"Message from {job.kind} agent"
    elif task is not None:
        agent_profile_id = task.assigned_agent_id
        source_task_id = task.id

    if agent_profile_id:
        profile = db.session.get(AgentProfile, agent_profile_id)
        if profile and profile.display_name:
            title = f"Message from {profile.display_name}"

    body = msg.content or ""
    # Deep-link to the chat panel on the job page. The agent-side id
    # IS the job id, so /jobs/<msg.task_id> resolves cleanly. View=chat
    # lands the user on the live thread for an in-context reply rather
    # than an inline reply box on the home pane.
    chat_url = f"/jobs/{msg.task_id}?view=chat" if msg.task_id else None
    memo = create_memo(
        kind="agent_message",
        category="info",
        title=title,
        body=body[:1000],
        priority="normal",
        url=chat_url,
        cta_label="Open chat" if chat_url else None,
        cta_action="open" if chat_url else None,
        source_agent_id=agent_profile_id,
        source_task_id=source_task_id,
        extra={
            "agent_message_id": msg.id,
            "task_id": msg.task_id,
        },
    )
    logger.info(
        f"[outbox] Emitted user memo for AgentMessage #{msg.id} "
        f"(agent_profile_id={agent_profile_id}, task_id={msg.task_id})"
    )
    return memo


def backfill_user_message_memos():
    """One-shot startup pass: for any from_agent AgentMessage with
    recipient="user" that doesn't have a Memo pointing at it, mint
    one. Catches messages that landed before the memo emission was
    wired (or any case where _emit_user_memo silently failed during
    the live request).

    Idempotent — uses Memo.extra.agent_message_id as the dedup key.
    Skips messages older than 7 days so a one-time backfill doesn't
    flood the inbox with stale ancient threads.
    """
    from datetime import datetime, timezone, timedelta
    from planet_maiko.models.agent_message import AgentMessage
    from planet_maiko.models.memo import Memo
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_job import AgentJob

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    candidates = (
        AgentMessage.query
        .filter(AgentMessage.direction == "from_agent")
        .filter(AgentMessage.recipient == "user")
        .filter(AgentMessage.created_at >= cutoff)
        .all()
    )
    if not candidates:
        return
    # Build a dedup set in one query — pulling Memo.extra.agent_message_id
    # via JSON path. SQLite supports json_extract; for portability we
    # pull all agent_message memos and filter in-Python (the set is small).
    existing_msg_ids = set()
    for memo in Memo.query.filter_by(kind="agent_message").all():
        mid = (memo.extra or {}).get("agent_message_id")
        if mid is not None:
            existing_msg_ids.add(mid)

    minted = 0
    for m in candidates:
        if m.id in existing_msg_ids:
            continue
        # Resolve job/task to compose the memo. Same priority as the
        # live path: AgentJob first, then Task.
        job = db.session.get(AgentJob, m.task_id) if m.task_id else None
        task = None
        if job is None:
            task = db.session.get(Task, m.task_id) if m.task_id else None
        try:
            _emit_user_memo(m, job=job, task=task)
            minted += 1
        except Exception as e:
            logger.warning(
                f"[outbox] Backfill memo failed for AgentMessage #{m.id}: {e}"
            )
    if minted:
        db.session.commit()
        logger.info(f"[outbox] Backfilled {minted} user-message memo(s)")




def _get_repo_for_task(task_id):
    """Extract repo from task metadata."""
    from planet_maiko.models.task import Task
    task = db.session.get(Task, task_id)
    if task and task.extra:
        return task.extra.get("repo")
    return None


@agents_bp.route("/agents/<job_id>/messages", methods=["GET"])
def get_all_messages(job_id):
    """Get full conversation history for an agent (both directions)."""
    messages = (
        AgentMessage.query
        .filter_by(task_id=job_id)
        .order_by(AgentMessage.created_at.asc())
        .all()
    )
    return jsonify([m.to_dict() for m in messages])


# Persistent session store: task_id → {session_id, working_path}.
# Helpers live in planet_maiko.agents.sessions; routes stay here so
# they can decorate against agents_bp.

@agents_bp.route("/agents/<task_id>/session", methods=["POST"])
def register_session(task_id):
    """Register a Claude Code session ID for an agent task."""
    data = request.get_json()
    session_id = data.get("session_id")
    if session_id:
        _set_session(task_id, session_id, data.get("working_path", ""))
    return jsonify({"status": "ok"})


@agents_bp.route("/agents/<task_id>/session", methods=["GET"])
def get_session(task_id):
    """Get the Claude Code session ID for an agent task."""
    info = _get_sessions().get(task_id, {})
    return jsonify({"session_id": info.get("session_id") if isinstance(info, dict) else info})


@agents_bp.route("/agents/requests", methods=["GET"])
def pack_requests():
    """Pupdates where the pack is actively waiting on the user.

    Deliberately SQL-level and cheap — this is the "your pack needs
    you" widget that sits on Home and polls every ~30s. The LLM-backed
    overview pane updates on its own cadence (~4h, plus event
    invalidation); that's the wrong shape for "an agent just asked for
    a plan review, show me now."

    Pack-internal only: plan waiting, review ready, agent stuck,
    proposal, working on feedback. External teammate events
    (pr_review_requested) are NOT included here — the automation
    already spawns a review Task for those, and the Task flows through
    the review-queue memo, so surfacing them here too would just be
    duplicate noise.
    """
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile

    AGENT_REQUEST_TYPES = (
        "agent_plan_for_approval",       # coding agent's plan waiting for user approval
        "agent_ready_for_review",        # review OR coding agent done, via MCP reply path
        "pr_review_complete",            # review agent done, via brain-cycle fallback path
        "agent_stuck",                   # agent blocked, needs user help
        "agent_proposal",                # "From Maiko" proposal in the approval queue
        "agent_working_on_feedback",     # transient: agent iterating on PR-review comments
    )

    limit = min(int(request.args.get("limit") or 10), 30)
    rows = (
        Pupdate.query
        .filter(Pupdate.type.in_(AGENT_REQUEST_TYPES))
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .order_by(Pupdate.timestamp.desc())
        .limit(limit * 2)  # overshoot; we may skip some that are held
        .all()
    )
    rows = [p for p in rows if not (p.extra or {}).get("held")][:limit]

    # Resolve agent display_name via extra.agent_id so the widget can
    # render "Yoshi needs a plan review" instead of "agent needs a plan
    # review". Batch lookup — one query for all unique agent_ids.
    agent_ids = {(p.extra or {}).get("agent_id") for p in rows}
    agent_ids.discard(None)
    name_by_id = {}
    if agent_ids:
        for a in AgentProfile.query.filter(AgentProfile.id.in_(agent_ids)).all():
            name_by_id[a.id] = a.display_name

    out = []
    for p in rows:
        extra = p.extra or {}
        d = p.to_dict()
        d["agent_name"] = name_by_id.get(extra.get("agent_id"))
        out.append(d)
    return jsonify(out)


@agents_bp.route("/agents/conflicts", methods=["GET"])
def get_conflicts():
    """Get recent conflict warnings between agents."""
    conflict_msgs = (
        AgentMessage.query
        .filter_by(sender="maiko", message_type="conflict_warning")
        .order_by(AgentMessage.created_at.desc())
        .limit(20)
        .all()
    )
    return jsonify([m.to_dict() for m in conflict_msgs])


# --- Hook Handlers ---
# Called by Claude Code hook scripts (hooks/*.py) to report events back
# to Planet Maiko. All endpoints are fire-and-forget from the hook side.

@agents_bp.route("/hooks/post-tool-use", methods=["POST"])
def hook_post_tool_use():
    """Handle post-tool-use hook events (git commit, git push).

    Accepts both `job_id` (canonical) and `task_id` (alternate, for
    agent hooks that send the older field name).
    """
    from datetime import datetime, timezone
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile

    data = request.get_json()
    job_id = data.get("job_id") or data.get("task_id", "")
    agent_id = data.get("agent_id", "")
    event = data.get("event", "tool_use")
    message = data.get("message", "")

    pupdate = Pupdate(
        id=f"hook-{agent_id}-{event}-{uuid.uuid4().hex[:8]}",
        source="agent",
        source_id=f"agent/{agent_id}",
        type="agent_update",
        priority="low",
        title=f"Agent {event.replace('_', ' ')}",
        body=message,
        tags=[job_id, "agent", "hook"],
        extra={
            "agent_id": agent_id,
            "job_id": job_id,
            "event": event,
        },
    )
    db.session.add(pupdate)

    # Update agent's last_active_at
    profile = db.session.get(AgentProfile, agent_id)
    if profile:
        profile.last_active_at = datetime.now(timezone.utc)

    db.session.commit()
    return jsonify({"status": "ok"}), 201


@agents_bp.route("/hooks/notification", methods=["POST"])
def hook_notification():
    """Handle notification hook: create milestone pupdate.

    Accepts both `job_id` (canonical) and `task_id` (alternate fallback).
    """
    from datetime import datetime, timezone
    from planet_maiko.models.pupdate import Pupdate

    data = request.get_json()
    job_id = data.get("job_id") or data.get("task_id", "")
    agent_id = data.get("agent_id", "")
    title = data.get("title", "Agent notification")
    body = data.get("body", "")

    pupdate = Pupdate(
        id=f"hook-notify-{agent_id}-{uuid.uuid4().hex[:8]}",
        source="agent",
        source_id=f"agent/{agent_id}",
        type="agent_milestone",
        priority="normal",
        title=title,
        body=body,
        actionable=True,
        action_hint="Review agent milestone",
        tags=[job_id, "agent", "milestone"],
        extra={
            "agent_id": agent_id,
            "job_id": job_id,
        },
    )
    db.session.add(pupdate)
    db.session.commit()
    return jsonify({"status": "ok"}), 201


@agents_bp.route("/hooks/subagent-stop", methods=["POST"])
def hook_subagent_stop():
    """Handle subagent-stop hook. Subagent completion is internal noise —
    we acknowledge the hook so Claude Code is happy but don't surface it
    as a pupdate. The parent agent's status updates are what the user
    actually cares about.
    """
    return jsonify({"status": "ok"}), 200
