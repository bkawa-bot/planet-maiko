import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from flask import Blueprint, current_app, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.agents.brain_session import run_skill, get_status as brain_status, ONE_SHOT_ROLE_FOR_TYPE
from planet_maiko.agents.coding_agent import prepare, list_prepared, cleanup
from planet_maiko.agents.monitor import get_agent_activity, get_queued_agent_tasks, process_agent_pupdates, get_stuck_agents
from planet_maiko.agents.skills import list_skills

logger = logging.getLogger(__name__)

agents_bp = Blueprint("agents", __name__)


_VALID_REVIEW_VERDICTS = {"approve", "approve_with_comments", "soft_block", "hard_block"}


def _parse_verdict_and_summary(content):
    """Pull the required `VERDICT:` + `SUMMARY:` lines out of a review
    agent's ready_for_review body.

    Protocol says the first two non-blank lines of the content are:

        VERDICT: approve | approve_with_comments | soft_block | hard_block
        SUMMARY: <one or two sentences>

    Case-insensitive on the label; tolerates extra whitespace. Returns
    (verdict, summary) — either value can be None when the tag was
    absent or malformed. An unknown verdict keyword is dropped too, so
    the stored value is always one of the enum or None.

    We don't fail the ready_for_review on missing verdict — old-shape
    reviews that only produce a long prose body still succeed (the
    artifact is preserved); the banner just won't have anything to
    show until the agent produces a new one in the new shape.
    """
    import re as _re
    verdict = None
    summary = None
    if not content:
        return verdict, summary
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _re.match(r"^verdict\s*:\s*(\S+)", stripped, _re.IGNORECASE)
        if m and verdict is None:
            candidate = m.group(1).strip().lower()
            if candidate in _VALID_REVIEW_VERDICTS:
                verdict = candidate
            continue
        m = _re.match(r"^summary\s*:\s*(.+)$", stripped, _re.IGNORECASE)
        if m and summary is None:
            summary = m.group(1).strip()[:500]
            continue
        # Stop once we've passed the header and hit a line that isn't
        # a known tag — SUMMARY can be continued on the next line but
        # anything else ends the search.
        if verdict is not None and summary is not None:
            break
    return verdict, summary


def _spawn_one_shot_thread(task_id, working_path):
    """Re-fire the autonomous run for a one-shot task on the unified
    headless flow (same as a fresh assign would do — claude --print
    in the worktree, agent uses the channel MCP to reply
    ready_for_review with the report content).

    Used by the rerun endpoint and by the cycle's safety-net
    execute phase. No-op if the task or its agent has gone away.
    """
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            try:
                from planet_maiko.models.task import Task
                from planet_maiko.models.agent_profile import AgentProfile
                from planet_maiko.agents.coding_agent import _kickoff_agent_headless
                task = db.session.get(Task, task_id)
                if not task:
                    logger.warning(f"[one-shot] Task {task_id} vanished before run")
                    return
                if task.status not in ("new", "blocked"):
                    return  # Already running or done — let the cycle handle it
                if not task.assigned_agent_id:
                    logger.warning(f"[one-shot] Task {task_id} has no assigned agent")
                    return
                profile = db.session.get(AgentProfile, task.assigned_agent_id)
                role = (profile.role if profile else None) or "investigation"
                _kickoff_agent_headless(
                    task.assigned_agent_id, working_path, task_id,
                    branch_name=None, plan_first=False, role=role,
                )
            except Exception as e:
                logger.exception(f"[one-shot] kickoff failed for {task_id}: {e}")

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
    # Worktree is always on + kickoff is always deferred until after
    # prepare() returns, so the old use_worktree / auto_kickoff body
    # params don't do anything. Kept ignoring them here for back-compat
    # with any UI client still sending them.
    plan_first = bool(data.get("plan_first", False))
    branch_name = data.get("branch_name")

    if not task_id or not profile_id:
        return jsonify({"error": "task_id and profile_id are required"}), 400

    task = db.get_or_404(Task, task_id)
    profile = db.get_or_404(AgentProfile, profile_id)
    role = profile.role or "coding"

    # Resolve repo_path. For coding the user picks one in the modal;
    # for review/investigation/cartographer the task's scope plus
    # repo_roots determines it (no UI input needed).
    if role in ("review", "investigation", "cartographer"):
        from planet_maiko.orchestration import resolve_repo_path, scope_for_task
        repo = scope_for_task(task)
        local_path = resolve_repo_path(repo)
        if not local_path:
            return jsonify({"error": f"No local clone found for {repo or 'this task'}"}), 400
        repo_path = local_path
        # Coerce task.type so the cycle's "find one-shot tasks" query
        # picks this up if the kickoff thread dies and we need a retry.
        if task.type not in ONE_SHOT_ROLE_FOR_TYPE:
            task.type = {
                "review": "review",
                "investigation": "investigation",
                "cartographer": "cartograph",
            }[role]
        if task.status not in ("blocked", "done"):
            task.status = "new"
    else:
        if not repo_path:
            return jsonify({"error": "repo_path is required. Select a repo in the assign modal."}), 400
        if not os.path.isdir(repo_path):
            return jsonify({"error": f"Repository path not found: {repo_path}"}), 400
        if not os.path.isdir(os.path.join(repo_path, ".git")):
            return jsonify({"error": f"Not a git repository: {repo_path}"}), 400

    # Build the prompt that lands in TASK.md. Shared with the pack
    # dispatcher and the brain cycle's safety-net executor so every
    # entry point composes the same context.
    from planet_maiko.orchestration import build_task_prompt
    full_prompt = build_task_prompt(task, role, data.get("custom_prompt", ""))

    try:
        result = prepare(
            task_id=task_id,
            task_title=task.title,
            prompt=full_prompt,
            repo_path=repo_path,
            branch_prefix=branch_name or "maiko",
            auto_kickoff=False,
            use_worktree=True,
            agent_profile_id=profile.id,
            role=role,
        )
    except Exception as e:
        return jsonify({"error": f"Agent preparation failed: {str(e)}"}), 500
    if not result:
        return jsonify({"error": "Failed to prepare agent"}), 500

    branch = result.get("branch")
    working_path = result.get("working_path")

    from planet_maiko.agents.coding_agent import _kickoff_agent_headless
    kickoff = _kickoff_agent_headless(
        profile.id, working_path, task_id,
        branch_name=None,  # always worktree mode
        plan_first=plan_first if role == "coding" else False,
        role=role,
    )
    result["kickoff_result"] = kickoff

    task.assigned_agent_id = profile.id
    if task.status == "new":
        task.status = "in_progress"
    extra = dict(task.extra or {})
    if working_path:
        extra["working_path"] = working_path
    if branch:
        extra["branch"] = branch
    if plan_first and role == "coding":
        extra["plan_first"] = True
    task.extra = extra
    db.session.commit()

    return jsonify({
        "task": task.to_dict(),
        "agent": profile.to_dict(),
        "mode": role,
        "worktree": result,
    }), 201


# _build_task_prompt moved to planet_maiko.orchestration.build_task_prompt
# so the pack dispatcher + brain cycle can use the same composer.


def _launch_terminal(cmd):
    # Open a new terminal window that runs `cmd` and stays open.
    #
    # All three platforms route through a temp script file (.sh on
    # macOS / Linux, .bat on Windows). When `cmd` contains double
    # quotes -- and ours always does, because we pass an initial
    # prompt to claude in quotes -- the platform-native incantations
    # all break in different ways:
    #   * macOS: osascript's `do script "..."` collapses on the
    #     first inner quote, emitting AppleScript's "an identifier
    #     can't go after this" error before anything runs.
    #   * Windows: `cmd /c start cmd /k "..."` mispairs the inner
    #     quotes with start's title arg.
    #   * Linux: bash -c handles quotes ok but is inconsistent across
    #     terminals.
    # A script file's contents are plain text -- no shell or
    # AppleScript parser sees the quotes. We just hand the launcher
    # a path.
    import sys as _sys
    import subprocess as _subprocess
    import tempfile as _tempfile
    import os as _os

    if _sys.platform == "darwin":
        with _tempfile.NamedTemporaryFile(
            "w", suffix=".sh", delete=False, encoding="utf-8",
        ) as f:
            f.write("#!/bin/bash\n")
            f.write(cmd + "\n")
            sh_path = f.name
        _os.chmod(sh_path, 0o755)
        # Telling Terminal to "do script <path>" runs the file; no
        # quotes inside the script body to worry about. If the user
        # closes the window the .sh stays in /tmp and is reaped by
        # the OS's normal temp-file cleanup.
        _subprocess.Popen([
            "osascript", "-e",
            f'tell application "Terminal" to do script "{sh_path}"',
        ])
        return

    if _sys.platform == "win32":
        with _tempfile.NamedTemporaryFile(
            "w", suffix=".bat", delete=False, encoding="utf-8",
        ) as f:
            f.write("@echo off\r\n")
            f.write(cmd + "\r\n")
            bat_path = f.name
        # `start "" cmd /k <bat>` — the empty "" is required as the
        # window-title placeholder, otherwise start treats the next
        # quoted thing as the title and the actual command never runs.
        _subprocess.Popen(
            ["cmd", "/c", "start", "", "cmd", "/k", bat_path],
            shell=False,
        )
        return

    # Linux: same approach for consistency.
    with _tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8",
    ) as f:
        f.write("#!/bin/bash\n")
        f.write(cmd + "\n")
        sh_path = f.name
    _os.chmod(sh_path, 0o755)
    for term in ["gnome-terminal", "xterm", "konsole"]:
        try:
            _subprocess.Popen([term, "--", "bash", sh_path])
            return
        except FileNotFoundError:
            continue


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
        _launch_terminal(attach_cmd)
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
        from planet_maiko.config import user_now
        now_local = user_now()
        title_map = {
            "morning-brief": f"Morning Brief — {now_local.strftime('%B %d')}",
            "brainstorm": f"Brainstorm — {now_local.strftime('%B %d')}",
            "evening-wrap": f"Evening Wrap — {now_local.strftime('%B %d')}",
            "investigate": f"Investigation — {now_local.strftime('%B %d %H:%M')}",
            "repo-analysis": f"Repo Analysis — {now_local.strftime('%B %d')}",
        }
        sr = SkillResult(
            skill_name=skill_name,
            title=title_map.get(skill_name, f"{skill_name} — {now_local.strftime('%B %d %H:%M')}"),
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


@agents_bp.route("/agents/<task_id>/rerun", methods=["POST"])
def rerun_agent(task_id):
    """Re-fire the autonomous one-shot run for a review/investigation
    task that's stuck on "Starting up" — the original headless run
    silently died (claude crashed, MCP failed to load, network blip)
    and the agent never sent its first pupdate, so the UI has no
    way to know what went wrong. This kicks a fresh thread that
    re-uses the same worktree and session_id so the user's View
    Session is still valid afterwards.

    No-op for coding tasks — those don't have a single "skill" to
    re-run; the user should use Relaunch to open a terminal.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE

    task = db.session.get(Task, task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    if task.type not in ONE_SHOT_ROLE_FOR_TYPE:
        return jsonify({"error": f"task type '{task.type}' isn't a one-shot role"}), 400
    if not task.assigned_agent_id:
        return jsonify({"error": "task has no assigned agent"}), 400

    working_path = (task.extra or {}).get("working_path")
    if not working_path or not os.path.isdir(working_path):
        return jsonify({"error": "no worktree on disk for this task — re-assign the agent"}), 400

    # Reset to "new" so _spawn_one_shot_thread doesn't bail on the
    # "already running / done" guard. The unified kickoff inside
    # that helper re-fires the same headless flow assign uses.
    if task.status == "in_progress":
        task.status = "new"
    db.session.commit()

    _spawn_one_shot_thread(task.id, working_path)
    return jsonify({"status": "rerunning", "task_id": task.id, "working_path": working_path}), 202


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

    # For one-shot tasks (review / investigation / repo_analysis), the
    # ready_for_review reply IS the report. Parse PATTERN: /
    # PROPOSAL: / CONFIDENCE: blocks out, save the cleaned content as
    # task.extra.artifact, and mark the task done. Coding tasks use
    # ready_for_review to mean "go look at the diff" and stay in
    # in_progress until the user explicitly approves — different
    # semantics, gated by task.type below.
    message_type = data.get("message_type", "message")
    if message_type == "ready_for_review":
        from planet_maiko.models.task import Task as _Task
        from planet_maiko.models.agent_profile import AgentProfile as _AgentProfile
        from planet_maiko.agents.brain_session import ONE_SHOT_ROLE_FOR_TYPE
        from planet_maiko.brain.learning.agent_output import parse_and_apply_blocks
        t = db.session.get(_Task, task_id)

        # Parse VERDICT + SUMMARY for any ready_for_review, not just
        # review tasks. Coding agents self-assess the same way — the
        # banner shows the self-verdict (approve / approve_with_comments
        # / soft_block / hard_block) at the top of their diff page.
        # If the agent omitted the header, both parse to None and
        # nothing is stored; everything else still works.
        if t:
            verdict, summary = _parse_verdict_and_summary(data.get("content") or "")
            if verdict or summary:
                extra = dict(t.extra or {})
                if verdict:
                    extra["review_verdict"] = verdict
                if summary:
                    extra["review_summary"] = summary
                t.extra = extra

        if t and t.type in ONE_SHOT_ROLE_FOR_TYPE:
            ag = db.session.get(_AgentProfile, t.assigned_agent_id) if t.assigned_agent_id else None
            try:
                parsed = parse_and_apply_blocks(
                    data["content"], agent=ag, task=t,
                    repo=(t.extra or {}).get("repo"),
                )
                cleaned = parsed.get("cleaned_output", data["content"])
                extra = dict(t.extra or {})
                extra["artifact"] = cleaned[:16000]
                extra["patterns_emitted"] = parsed.get("patterns_emitted", 0)
                extra["proposals_emitted"] = parsed.get("proposals_emitted", 0)
                if parsed.get("confidence"):
                    extra["confidence"] = parsed["confidence"]

                # review / pr_review task types get their verdict + summary
                # from the outer parse above (applies to all ready_for_review
                # regardless of task type). Here we just branch on role for
                # status + worktree-cleanup semantics.
                is_review_task = t.type in ("review", "pr_review")
                extra["completed_at"] = datetime.now(timezone.utc).isoformat()
                t.extra = extra

                # Reviews keep their worktree around so the user can
                # load the diff + inline comments at /tasks/:id/review.
                # The task also stays in an awaiting-user state rather
                # than jumping straight to "done" — the user closes it
                # explicitly via the diff page. Investigations and
                # other one-shots clean up immediately as before.
                if is_review_task:
                    t.status = "review"
                else:
                    t.status = "done"

                if ag:
                    ag.last_active_at = datetime.now(timezone.utc)
                logger.info(
                    f"[outbox] {t.type} task {task_id} done — "
                    f"{parsed.get('patterns_emitted', 0)} patterns, "
                    f"{parsed.get('proposals_emitted', 0)} proposals"
                )

                if not is_review_task:
                    # Non-review one-shots tear down immediately — the
                    # artifact is saved, the scratch dir's not doing
                    # anything for them anymore. Reviews keep it.
                    try:
                        from planet_maiko.agents.coding_agent import cleanup_task_worktree
                        cleanup_task_worktree(t)
                    except Exception as e:
                        logger.warning(f"[outbox] worktree cleanup failed for {task_id}: {e}")
            except Exception as e:
                logger.warning(f"[outbox] artifact save failed for {task_id}: {e}")

    # Agent-reported insight: tribal / operational knowledge (tooling
    # tips, migration state, team conventions) that should be injected
    # into future agents' CLAUDE.md. Lands as a pending Insight so the
    # user reviews before it goes into every new session's prompt.
    # Intentionally separate from Learnings — no LoRA training, no
    # confidence scoring, just a note.
    if message_type == "insight":
        try:
            from planet_maiko.models.insight import Insight
            from planet_maiko.models.task import Task as _Task
            from planet_maiko.models.agent_profile import AgentProfile as _AP
            t = db.session.get(_Task, task_id)
            repo_scope = None
            if t:
                extra = t.extra or {}
                repo_scope = extra.get("repo") or extra.get("repository")

            # Cartographer replies are Repo Overview docs — auto-tag so
            # _build_playbook_section promotes them on approve, and
            # give them more room since the overview format is a
            # structured multi-section markdown doc.
            author_role = None
            if t and t.assigned_agent_id:
                author = db.session.get(_AP, t.assigned_agent_id)
                if author:
                    author_role = author.role
            is_cartographer = author_role == "cartographer"

            tags = list(data.get("tags") or [])
            if is_cartographer:
                for t_ in ("overview", "cartographer"):
                    if t_ not in tags:
                        tags.append(t_)

            max_len = 8000 if is_cartographer else 2000
            ins = Insight(
                text=(data["content"] or "").strip()[:max_len],
                repo_scope=repo_scope,
                tags=tags,
                author_agent_id=(t.assigned_agent_id if t else None),
                status="pending",
                source_message_id=msg.id,
            )
            db.session.add(ins)
            logger.info(
                f"[outbox] Agent insight recorded (task={task_id}, "
                f"repo={repo_scope or 'global'}, tags={tags}): {ins.text[:80]}"
            )
        except Exception as e:
            logger.warning(f"[outbox] insight save failed for {task_id}: {e}")

    if message_type not in ("status", "feedback", "insight", "summary"):
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

        # Pull the PR URL off the task (if the agent has opened one)
        # so the pupdate card can link straight to GitHub. Covers both
        # storage locations: task.url (set by approve / auto-open flows)
        # and task.extra.pr_url (set by the agent MCP reply path).
        pr_url = None
        if task:
            candidate = task.url or (task.extra or {}).get("pr_url")
            if candidate and "github.com" in candidate:
                pr_url = candidate

        # Carry forward the original ask + boundary + when it was asked
        # so the user can reload context in one glance — "you asked 3
        # hours ago: 'look at the auth bug' (must not: touch billing)".
        # The single biggest cost of parallel agents is the reload-tax
        # when one returns 3h after you kicked it off and you've since
        # swapped context five times. Store on pupdate.extra so the
        # frontend can render without an extra task fetch.
        original_ask = ""
        original_non_goals = ""
        asked_at_iso = None
        if task is not None:
            t_extra = task.extra or {}
            original_ask = (
                t_extra.get("user_request")
                or t_extra.get("description")
                or t_extra.get("body")
                or task.title
                or ""
            ).strip()
            raw_ng = t_extra.get("non_goals") or ""
            if isinstance(raw_ng, list):
                original_non_goals = "; ".join(str(g).strip() for g in raw_ng if str(g).strip())
            else:
                original_non_goals = str(raw_ng).strip()
            if task.created_at:
                asked_at_iso = task.created_at.isoformat() if hasattr(task.created_at, "isoformat") else str(task.created_at)

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
            url=pr_url,
            tags=[task_id, "agent-message"],
            extra={
                "task_id": task_id,
                "agent_id": task.assigned_agent_id if task else None,
                "message_type": message_type,
                "pr_url": pr_url,
                "original_ask": original_ask[:500],
                "original_non_goals": original_non_goals[:500] if original_non_goals else "",
                "asked_at": asked_at_iso,
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
            source_message_id=msg.id,
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


@agents_bp.route("/agents/requests", methods=["GET"])
def pack_requests():
    """Pupdates where the pack is actively waiting on the user.

    Deliberately SQL-level and cheap — this is the "your pack needs
    you" widget that sits on Home and polls every ~30s. The LLM-backed
    overview pane updates on its own cadence (~4h, plus event
    invalidation); that's the wrong shape for "an agent just asked for
    a plan review, show me now."

    Filters to agent-originated types only: plans waiting for approval,
    diffs ready for review (from either the agent's MCP reply OR the
    brain cycle's safety-net synthesizer), stuck agents, proposals.
    Investigation reports go through the overview's artifact modal so
    they're not here. External GitHub events (pr_review_requested,
    pr_changes_requested) are intentionally excluded — those are
    teammates asking for your attention, not your pack's output.

    Held pupdates (from focus mode) are hidden here too, matching the
    main /api/pupdates behavior.
    """
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko.models.agent_profile import AgentProfile

    AGENT_REQUEST_TYPES = (
        "agent_plan_for_approval",   # coding agent's plan waiting for user approval
        "agent_ready_for_review",    # review OR coding agent done, via MCP reply path
        "pr_review_complete",        # review agent done, via brain-cycle fallback path
        "agent_stuck",               # agent blocked, needs user help
        "agent_proposal",            # "From Maiko" proposal in the approval queue
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
