import logging
import os
import threading
import uuid
from flask import Blueprint, current_app, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.agents.brain_session import run_skill, get_status as brain_status
from planet_maiko.agents.coding_agent import prepare, list_prepared, cleanup
from planet_maiko.agents.monitor import get_agent_activity, get_queued_agent_tasks, process_agent_pupdates, get_stuck_agents
from planet_maiko.agents.skills import list_skills

logger = logging.getLogger(__name__)

agents_bp = Blueprint("agents", __name__)


def _spawn_one_shot_thread(task_id, working_path):
    """Fire a daemon thread that runs execute_one_shot_task for a
    freshly-assigned review/investigation task.

    Runs in its own app context so the thread has DB access. Any
    failure is logged but doesn't reach the caller — the cycle's
    execute-one-shot phase picks up stragglers on its next tick.
    """
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            try:
                from planet_maiko.models.task import Task
                from planet_maiko.agents.brain_session import execute_one_shot_task
                task = db.session.get(Task, task_id)
                if not task:
                    logger.warning(f"[assign-thread] Task {task_id} vanished before run")
                    return
                if task.status not in ("new", "blocked"):
                    return  # Already running or done — let the cycle handle it
                execute_one_shot_task(task, working_dir=working_path)
            except Exception as e:
                logger.exception(f"[assign-thread] execute_one_shot_task failed for {task_id}: {e}")

    threading.Thread(target=_run, daemon=True, name=f"one-shot-{task_id}").start()


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
        schedule_interval_minutes=data.get("schedule_interval_minutes"),
        creates_pupdates=data.get("creates_pupdates", False),
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
    if "schedule_interval_minutes" in data:
        skill.schedule_interval_minutes = data["schedule_interval_minutes"] or None
    if "creates_pupdates" in data:
        skill.creates_pupdates = data["creates_pupdates"]
    db.session.commit()
    return jsonify(skill.to_dict())


@agents_bp.route("/skills/<skill_id>", methods=["DELETE"])
def delete_skill(skill_id):
    """Delete a custom skill (cannot delete defaults)."""
    from planet_maiko.models.custom_skill import CustomSkill
    skill = db.get_or_404(CustomSkill, skill_id)
    if skill.is_default:
        return jsonify({"error": "Cannot delete default skills. Edit them instead."}), 400
    db.session.delete(skill)
    db.session.commit()
    return jsonify({"status": "deleted"})


@agents_bp.route("/agents/assign", methods=["POST"])
def assign_agent():
    """Assign an agent to a task.

    Coding agents: prepares a worktree + CLAUDE.md as before. Requires
    repo_path.

    Review / investigation agents: also prepares a worktree (so the
    user can "dig deeper" later by attaching to it), then fires a
    background thread that runs the one-shot skill immediately. No
    repo_path needed — resolved from config.github.repo_roots using
    the task's repo. Returns 201 right away; the thread writes the
    result pupdate when it completes.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.agents.coding_agent import prepare
    from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE

    data = request.get_json()
    task_id = data.get("task_id")
    profile_id = data.get("profile_id")
    repo_path = data.get("repo_path", "")
    # Worktree is always on — every agent role works on an isolated copy
    # of the repo, no exceptions. Keeps the assign flow uniform across
    # coding / review / investigation and avoids the "I forgot to check
    # the box and now my main branch is dirty" footgun.
    use_worktree = True
    auto_kickoff = data.get("auto_kickoff", False)
    plan_first = bool(data.get("plan_first", False))
    branch_name = data.get("branch_name")

    if not task_id or not profile_id:
        return jsonify({"error": "task_id and profile_id are required"}), 400

    task = db.get_or_404(Task, task_id)
    profile = db.get_or_404(AgentProfile, profile_id)

    # Review / investigation: prepare a worktree (for later "dig
    # deeper"), fire a daemon thread that runs the skill, return
    # immediately. The thread writes the result pupdate.
    if profile.role in ("review", "investigation"):
        from planet_maiko.orchestration import resolve_repo_path, scope_for_task
        repo = scope_for_task(task)
        local_path = resolve_repo_path(repo)
        if not local_path:
            return jsonify({"error": f"No local clone found for {repo or 'this task'}"}), 400

        task.assigned_agent_id = profile.id
        if task.type not in ONE_SHOT_ROLE_FOR_TYPE:
            task.type = {"review": "review", "investigation": "investigation"}[profile.role]
        if task.status not in ("blocked", "done"):
            task.status = "new"

        prompt_parts = [task.title]
        if task.source_pupdate_id:
            from planet_maiko.models.pupdate import Pupdate
            source = db.session.get(Pupdate, task.source_pupdate_id)
            if source and source.body:
                prompt_parts.append(f"\n## Source Context\n\n{source.body}")
            if source and source.url:
                prompt_parts.append(f"\nSource URL: {source.url}")
        if task.url:
            prompt_parts.append(f"\nTask URL: {task.url}")
        full_prompt = "\n".join(prompt_parts)

        try:
            prep_result = prepare(
                task_id=task.id,
                task_title=task.title,
                prompt=full_prompt,
                repo_path=local_path,
                branch_prefix=branch_name or "maiko",
                auto_kickoff=False,
                use_worktree=True,
                agent_profile_id=profile.id,
                role=profile.role,
            )
        except Exception as e:
            return jsonify({"error": f"Prepare failed: {e}"}), 500
        if not prep_result:
            return jsonify({"error": "Prepare failed"}), 500

        extra = dict(task.extra or {})
        extra["working_path"] = prep_result["working_path"]
        extra["branch"] = prep_result["branch"]
        task.extra = extra
        db.session.commit()

        _spawn_one_shot_thread(task.id, prep_result["working_path"])

        return jsonify({
            "task": task.to_dict(),
            "agent": profile.to_dict(),
            "mode": profile.role,
            "worktree": prep_result,
        }), 201

    # Coding path below — unchanged.
    if not repo_path:
        return jsonify({"error": "repo_path is required. Select a repo in the assign modal."}), 400

    # Build rich prompt from task + source pupdate + project
    prompt_parts = [task.title]

    # Pull in source context (Linear issue body, GitHub PR description, etc.)
    if task.source_pupdate_id:
        from planet_maiko.models.pupdate import Pupdate
        source = db.session.get(Pupdate, task.source_pupdate_id)
        if source and source.body:
            prompt_parts.append(f"\n## Source Context\n\n{source.body}")
        if source and source.url:
            prompt_parts.append(f"\nSource URL: {source.url}")

    # Pull in project description
    if task.project_id:
        from planet_maiko.models.project import Project
        project = db.session.get(Project, task.project_id)
        if project and project.description:
            prompt_parts.append(f"\n## Project: {project.title}\n\n{project.description}")

    # Add task metadata
    if task.url:
        prompt_parts.append(f"\nTask URL: {task.url}")
    if task.tags:
        prompt_parts.append(f"\nTags: {', '.join(task.tags)}")

    # Add user's custom instructions if provided in request
    user_prompt = data.get("custom_prompt", "")
    if user_prompt:
        prompt_parts.append(f"\n## Additional Instructions\n\n{user_prompt}")

    full_prompt = "\n".join(prompt_parts)

    # Validate repo path exists and is a git repo
    if not os.path.isdir(repo_path):
        return jsonify({"error": f"Repository path not found: {repo_path}"}), 400
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return jsonify({"error": f"Not a git repository: {repo_path}"}), 400

    try:
        result = prepare(
            task_id=task_id,
            task_title=task.title,
            prompt=full_prompt,
            repo_path=repo_path,
            branch_prefix=branch_name or "maiko",
            auto_kickoff=False,  # we do our own headless kickoff below
            use_worktree=use_worktree,
            agent_profile_id=profile_id,
        )
    except Exception as e:
        return jsonify({"error": f"Agent preparation failed: {str(e)}"}), 500

    if not result:
        return jsonify({"error": "Failed to prepare agent"}), 500

    # Coding agents now run autonomously by default — headless subprocess,
    # no tmux, no terminal. User reviews the agent's diff in-app when
    # the agent reports ready_for_review. The old interactive launch is
    # preserved behind the explicit `auto_kickoff: true` flag for users
    # who still want to watch a terminal.
    from planet_maiko.agents.coding_agent import _kickoff_agent_headless, _kickoff_agent
    branch = result.get("branch")
    working_path = result.get("working_path")
    if auto_kickoff:
        kickoff = _kickoff_agent(
            profile_id, working_path, task_id,
            branch_name=branch if not use_worktree else None,
        )
    else:
        kickoff = _kickoff_agent_headless(
            profile_id, working_path, task_id,
            branch_name=branch if not use_worktree else None,
            plan_first=plan_first,
        )
    result["kickoff_result"] = kickoff

    # Link task to agent
    task.assigned_agent_id = profile_id
    if task.status == "new":
        task.status = "in_progress"
    # Persist the worktree info on the task itself so the frontend can
    # always surface a "Review diff" link — even if the agent never
    # sends ready_for_review, the task carries enough state to find
    # the worktree and render its diff.
    extra = dict(task.extra or {})
    if working_path:
        extra["working_path"] = working_path
    if branch:
        extra["branch"] = branch
    if plan_first:
        extra["plan_first"] = True
    task.extra = extra
    db.session.commit()

    return jsonify({
        "task": task.to_dict(),
        "agent": profile.to_dict(),
        "worktree": result,
    }), 201


def _find_claude_session_file(working_path, session_id):
    """Find the Claude Code session JSONL file for a given worktree + session ID.

    Claude stores sessions at ~/.claude/projects/{escaped-path}/{session_id}.jsonl
    where escaped-path replaces /, \\, and : each independently with -.
    On Windows, "C:\\Users\\foo" becomes "C--Users-foo" (double dash from : + \\).
    """
    if not working_path or not session_id:
        return None
    abs_path = os.path.abspath(working_path)
    escaped = abs_path.replace(":", "-").replace("\\", "-").replace("/", "-")
    candidates = [
        os.path.expanduser(f"~/.claude/projects/{escaped}/{session_id}.jsonl"),
        os.path.expanduser(f"~/.config/claude/projects/{escaped}/{session_id}.jsonl"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


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
    task_id = data.get("task_id", "")
    session_info = _get_sessions().get(task_id)

    # Fall back to the task's own record if the in-memory cache lost
    # track (e.g. older review/investigation tasks whose session was
    # registered before the cache was persistent, or any flow where
    # _set_session was skipped). The session_id + working_path stored
    # on task.extra are the canonical record.
    if not session_info:
        from planet_maiko.models.task import Task
        task = db.session.get(Task, task_id) if task_id else None
        if task and (task.extra or {}).get("session_id"):
            extra = task.extra
            session_info = {
                "session_id": extra["session_id"],
                "working_path": extra.get("working_path", ""),
            }

    if not session_info:
        return jsonify({"error": "No session found. Launch the agent first."}), 404

    session_id = session_info["session_id"]
    working_path = session_info.get("working_path", "")

    # 1. Try tmux attach (best experience — full interactive view)
    tmux_path = shutil.which("tmux")
    session_name = f"maiko-{task_id}"
    has_tmux = False
    if tmux_path:
        result = subprocess.run(
            [tmux_path, "has-session", "-t", session_name],
            capture_output=True,
        )
        has_tmux = result.returncode == 0

    mode = None
    if has_tmux:
        cmd = f"tmux attach -t {session_name}"
        mode = "tmux"
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
        if sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{cmd}"'])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", cmd], shell=True)
        else:
            for term in ["gnome-terminal", "xterm", "konsole"]:
                try:
                    subprocess.Popen([term, "--", "bash", "-c", cmd])
                    break
                except FileNotFoundError:
                    continue
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
        allowed_tools = ["mcp__maiko-channel"]
        try:
            from planet_maiko.config import load_config
            user_tools = load_config().get("brain", {}).get("allowed_tools", [])
            allowed_tools.extend(user_tools)
        except Exception:
            pass
        tools_flags = " ".join(f'--allowedTools "{t}"' for t in allowed_tools)
        checkout = f"git checkout {branch} && " if branch else ""
        initial_prompt = "Read TASK.md and CLAUDE.md in this directory. Begin working on the task following the protocol."

        # Generate a session ID upfront so we can resume later
        session_id = str(uuid.uuid4())
        if task_id:
            _set_session(task_id, session_id, path)

        attach_cmd = f'{checkout}cd {path} && claude --session-id {session_id} {tools_flags} "{initial_prompt}"'

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{attach_cmd}"'])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", attach_cmd], shell=True)
        else:
            for term in ["gnome-terminal", "xterm", "konsole"]:
                try:
                    subprocess.Popen([term, "--", "bash", "-c", attach_cmd])
                    break
                except FileNotFoundError:
                    continue
        info = _get_sessions().get(task_id, {})
        return jsonify({"status": "opened", "path": path, "tmux_attached": has_tmux_session, "session_id": info.get("session_id") if isinstance(info, dict) else info})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@agents_bp.route("/skills/<skill_name>/run", methods=["POST"])
def run_skill_endpoint(skill_name):
    """Run a skill through the brain session and save the result."""
    data = request.get_json() or {}
    context = data.get("context", {})
    working_dir = data.get("working_dir")

    result = run_skill(skill_name, context=context, working_dir=working_dir)

    # Auto-save successful results
    if result.get("success") and result.get("output"):
        from planet_maiko.models.skill_result import SkillResult
        from datetime import datetime
        title_map = {
            "morning-brief": f"Morning Brief — {datetime.now().strftime('%B %d')}",
            "brainstorm": f"Brainstorm — {datetime.now().strftime('%B %d')}",
            "pack-insights": f"Pack Insights — {datetime.now().strftime('%B %d')}",
            "investigate": f"Investigation — {datetime.now().strftime('%B %d %H:%M')}",
            "repo-analysis": f"Repo Analysis — {datetime.now().strftime('%B %d')}",
        }
        sr = SkillResult(
            skill_name=skill_name,
            title=title_map.get(skill_name, f"{skill_name} — {datetime.now().strftime('%B %d %H:%M')}"),
            content=result["output"],
        )
        db.session.add(sr)
        db.session.commit()
        result["result_id"] = sr.id

    return jsonify(result)


@agents_bp.route("/skill-results", methods=["GET"])
def list_skill_results():
    """List all skill results, optionally filtered by skill name."""
    from planet_maiko.models.skill_result import SkillResult
    skill_name = request.args.get("skill_name")
    query = SkillResult.query
    if skill_name:
        query = query.filter_by(skill_name=skill_name)
    results = query.order_by(SkillResult.created_at.desc()).limit(50).all()
    return jsonify([r.to_dict() for r in results])


@agents_bp.route("/skill-results/<int:result_id>", methods=["GET"])
def get_skill_result(result_id):
    """Get a single skill result."""
    from planet_maiko.models.skill_result import SkillResult
    sr = db.get_or_404(SkillResult, result_id)
    return jsonify(sr.to_dict())


@agents_bp.route("/agents", methods=["GET"])
def get_agents():
    """List all prepared agent worktrees."""
    return jsonify(list_prepared())


@agents_bp.route("/agents/activity", methods=["GET"])
def get_activity():
    """Get recent agent activity (pupdates from agents)."""
    return jsonify(get_agent_activity())


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

    Does NOT launch the agent unless auto_kickoff is True - sets up the
    worktree with TASK.md and CLAUDE.md, and notifies the user it's ready.
    """
    data = request.get_json()
    result = prepare(
        task_id=data["task_id"],
        task_title=data["task_title"],
        prompt=data["prompt"],
        repo_path=data["repo_path"],
        branch_prefix=data.get("branch_prefix", "maiko"),
        auto_kickoff=data.get("auto_kickoff", False),
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

@agents_bp.route("/agents/<task_id>/inbox", methods=["GET"])
def get_agent_inbox(task_id):
    """Get messages for an agent (channel polls this every ~15s).

    Query params:
        unread_only: "true" to only return unread messages (default: true)
        mark_read: "true" to auto-mark returned messages as read (default: true)
    """
    unread_only = request.args.get("unread_only", "true").lower() == "true"
    mark_read = request.args.get("mark_read", "true").lower() == "true"

    query = AgentMessage.query.filter_by(task_id=task_id, direction="to_agent")
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


@agents_bp.route("/agents/<task_id>/inbox", methods=["POST"])
def send_to_agent(task_id):
    """Send a message to an agent (from dashboard, brain, or user)."""
    data = request.get_json()
    msg = AgentMessage(
        task_id=task_id,
        direction="to_agent",
        sender=data.get("sender", "user"),
        content=data["content"],
        message_type=data.get("message_type", "message"),
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify(msg.to_dict()), 201


@agents_bp.route("/agents/<task_id>/nudge", methods=["POST"])
def nudge_agent(task_id):
    """User-triggered nudge: drop a message in the agent's inbox asking
    for a status check, and resume the claude session if there is one
    so the agent actually wakes up to read it.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile

    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404

    agent_name = "the agent"
    if task.assigned_agent_id:
        profile = db.session.get(AgentProfile, task.assigned_agent_id)
        if profile:
            agent_name = profile.display_name

    db.session.add(AgentMessage(
        task_id=task_id,
        direction="to_agent",
        sender="user",
        content=(
            "Hi! Just checking in — please post a quick status update "
            "via reply(message_type='status') so I know where you're at."
        ),
        message_type="message",
    ))
    db.session.commit()

    resumed = False
    working_path = (task.extra or {}).get("working_path")
    if working_path:
        try:
            from planet_maiko.api.diff_api import _resume_agent_with_review
            _resume_agent_with_review(task_id, working_path)
            resumed = True
        except Exception as e:
            logger.warning(f"[nudge] Resume failed for {task_id}: {e}")

    return jsonify({
        "status": "nudged",
        "agent": agent_name,
        "resumed": resumed,
    }), 201


@agents_bp.route("/agents/<task_id>/outbox", methods=["POST"])
def agent_sends_message(task_id):
    """Agent sends a message back (alternative to pupdate-based reporting)."""
    data = request.get_json()
    msg = AgentMessage(
        task_id=task_id,
        direction="from_agent",
        sender=data.get("sender", "agent"),
        content=data["content"],
        message_type=data.get("message_type", "message"),
    )
    db.session.add(msg)

    # Emit a pupdate so the user actually sees the message in their
    # inbox — otherwise it just sits in the Channel Log nobody checks.
    # Full content goes in the body; title gets a one-line preview.
    # Skip "status" messages (those are chatter), skip "feedback"
    # (the Signal creation below already surfaces it).
    message_type = data.get("message_type", "message")
    if message_type not in ("status", "feedback"):
        from planet_maiko.models.task import Task
        from planet_maiko.models.agent_profile import AgentProfile
        task = db.session.get(Task, task_id)
        agent_name = None
        if task and task.assigned_agent_id:
            agent = db.session.get(AgentProfile, task.assigned_agent_id)
            if agent:
                agent_name = agent.display_name
        agent_name = agent_name or "Agent"

        content = data["content"]
        preview = content.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "…"

        # Priority: stuck is high (blocked, needs help);
        # ready_for_review / plan_for_approval are high (user needs to act);
        # plain messages are normal.
        priority = "high" if message_type in ("stuck", "ready_for_review", "plan_for_approval") else "normal"
        type_label = {
            "done": "completed",
            "stuck": "is stuck",
            "ready_for_review": "ready for review",
            "plan_for_approval": "has a plan",
            "pr_opened": "opened PR",
            "message": "replied",
        }.get(message_type, "replied")

        # Distinct pupdate types so the UI can route them to different
        # actions: ready_for_review → "Review diff" button, others →
        # generic "Open task".
        pupdate_type = {
            "ready_for_review": "agent_ready_for_review",
            "plan_for_approval": "agent_plan_for_approval",
            "pr_opened": "agent_pr_opened",
            "done": "agent_done",
            "stuck": "agent_stuck",
        }.get(message_type, "agent_message")

        action_hint = {
            "ready_for_review": "Review diff",
            "plan_for_approval": "Review plan",
            "pr_opened": "Open PR",
            "stuck": "Help the agent",
            "done": "Open task",
        }.get(message_type, "Open task")

        from planet_maiko.models.pupdate import Pupdate
        pupdate = Pupdate(
            id=f"agent-msg-{task_id}-{uuid.uuid4().hex[:8]}",
            source="maiko",
            source_id=f"agent-msg/{task_id}/{msg.id or uuid.uuid4().hex[:8]}",
            type=pupdate_type,
            priority=priority,
            title=f"{agent_name} {type_label}: {preview}",
            body=content,
            actionable=True,
            action_hint=action_hint,
            tags=[task_id, "agent-message"],
            extra={
                "task_id": task_id,
                "agent_id": task.assigned_agent_id if task else None,
                "message_type": message_type,
            },
            # These pupdates already link back to the task — LLM triage
            # would otherwise spawn a duplicate task for every
            # ready_for_review / agent_done / agent_stuck event.
            brain_processed=True,
        )
        db.session.add(pupdate)

    # Agent reporting it just opened a PR (in response to an
    # approved message from the user). Parse the URL out of the
    # content and pin it onto the task so the rest of the pipeline
    # (pr_review_commented, _complete_review_task on merge) can
    # match comments / merges back to this task.
    if data.get("message_type") == "pr_opened":
        import re as _re
        from planet_maiko.models.task import Task as _Task
        content = data.get("content", "")
        match = _re.search(r"https?://[^\s]+", content or "")
        if match:
            pr_url = match.group(0).rstrip(".,;")
            _task = db.session.get(_Task, task_id)
            if _task:
                _extra = dict(_task.extra or {})
                _extra["pr_url"] = pr_url
                _task.url = pr_url
                _task.extra = _extra
                logger.info(f"[outbox] Stored pr_url for {task_id}: {pr_url}")

    # If feedback message, create a training signal with code context
    if data.get("message_type") == "feedback":
        metadata = data.get("metadata", {})
        category = metadata.get("feedback_category", "pattern")
        severity = metadata.get("feedback_severity", "suggestion")

        # Grab the most recent agent output as code context
        recent_agent_msg = (
            AgentMessage.query
            .filter_by(task_id=task_id, direction="from_agent")
            .order_by(AgentMessage.created_at.desc())
            .first()
        )
        code_context = recent_agent_msg.content[:3000] if recent_agent_msg else None

        from planet_maiko.models.signal import Signal
        signal = Signal(
            category=category,
            text=data["content"],
            source_type="session_feedback",
            severity=severity,
            repo=_get_repo_for_task(task_id),
            file_path=metadata.get("file_path"),
            code_context=code_context,
            # Session feedback carries an explicit category from the
            # agent — skip re-synthesis.
            synthesized=True,
        )
        db.session.add(signal)

        # Small immediate specialization penalty
        from planet_maiko.agents.profiles import record_session_feedback
        record_session_feedback(task_id, category, severity)

    db.session.commit()
    return jsonify(msg.to_dict()), 201


def _get_repo_for_task(task_id):
    """Extract repo from task metadata."""
    from planet_maiko.models.task import Task
    task = db.session.get(Task, task_id)
    if task and task.extra:
        return task.extra.get("repo")
    return None


@agents_bp.route("/agents/<task_id>/messages", methods=["GET"])
def get_all_messages(task_id):
    """Get full conversation history for a task (both directions)."""
    messages = (
        AgentMessage.query
        .filter_by(task_id=task_id)
        .order_by(AgentMessage.created_at.asc())
        .all()
    )
    return jsonify([m.to_dict() for m in messages])


# ---------------------------------------------------------------------------
# Persistent session store: task_id -> {session_id, working_path}
#
# Backed by a JSON file in the Maiko data dir so mappings survive server
# restarts. Lazy-loaded on first read, flushed on every write.
# ---------------------------------------------------------------------------

_agent_sessions = None  # loaded lazily by _get_sessions()
_SESSIONS_FILENAME = "agent-sessions.json"


def _sessions_path():
    from planet_maiko.paths import data_dir
    return os.path.join(data_dir(), _SESSIONS_FILENAME)


def _get_sessions():
    """Return the sessions dict, loading from disk on first access."""
    global _agent_sessions
    if _agent_sessions is None:
        path = _sessions_path()
        if os.path.exists(path):
            try:
                import json as _json
                with open(path, "r", encoding="utf-8") as f:
                    _agent_sessions = _json.load(f)
            except Exception:
                _agent_sessions = {}
        else:
            _agent_sessions = {}
    return _agent_sessions


def _save_sessions():
    """Flush the sessions dict to disk."""
    import json as _json
    path = _sessions_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(_agent_sessions, f, indent=2)


def _set_session(task_id, session_id, working_path=""):
    """Store a session mapping and persist to disk."""
    sessions = _get_sessions()
    sessions[task_id] = {"session_id": session_id, "working_path": working_path}
    _save_sessions()


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
    """Handle post-tool-use hook events (git commit, git push)."""
    from datetime import datetime, timezone
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile

    data = request.get_json()
    task_id = data.get("task_id", "")
    agent_id = data.get("agent_id", "")
    event = data.get("event", "tool_use")
    message = data.get("message", "")

    # Create an agent_update pupdate
    pupdate = Pupdate(
        id=f"hook-{agent_id}-{event}-{uuid.uuid4().hex[:8]}",
        source="agent",
        source_id=f"agent/{agent_id}",
        type="agent_update",
        priority="low",
        title=f"Agent {event.replace('_', ' ')}",
        body=message,
        tags=[task_id, "agent", "hook"],
        extra={
            "agent_id": agent_id,
            "task_id": task_id,
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


@agents_bp.route("/hooks/post-compact", methods=["POST"])
def hook_post_compact():
    """Handle post-compact hook: refresh agent's learning context."""
    from planet_maiko.brain.learning.processor import compile_brief

    data = request.get_json()
    task_id = data.get("task_id", "")
    agent_id = data.get("agent_id", "")

    if not task_id:
        return jsonify({"error": "task_id required"}), 400

    # Compile a fresh brief for this agent
    brief = compile_brief(agent_profile_id=agent_id)

    if not brief or brief == "No active learnings yet.":
        return jsonify({"status": "no_learnings"}), 200

    # Send the brief as an inbox message
    msg = AgentMessage(
        task_id=task_id,
        direction="to_agent",
        sender="maiko",
        content=f"Context refreshed after compaction. Here are the current coding guidelines:\n\n{brief}",
        message_type="context_refresh",
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify({"status": "ok", "brief_length": len(brief)}), 201


@agents_bp.route("/hooks/notification", methods=["POST"])
def hook_notification():
    """Handle notification hook: create milestone pupdate."""
    from datetime import datetime, timezone
    from planet_maiko.models.pupdate import Pupdate

    data = request.get_json()
    task_id = data.get("task_id", "")
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
        tags=[task_id, "agent", "milestone"],
        extra={
            "agent_id": agent_id,
            "task_id": task_id,
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
