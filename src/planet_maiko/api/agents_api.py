import os
from flask import Blueprint, jsonify, request
from planet_maiko.database import db
from planet_maiko.models.agent_message import AgentMessage
from planet_maiko.agents.brain_session import run_skill, get_status as brain_status
from planet_maiko.agents.coding_agent import prepare, list_prepared, cleanup
from planet_maiko.agents.monitor import get_agent_activity, process_agent_pupdates, get_stuck_agents
from planet_maiko.agents.skills import list_skills

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
    """Assign an agent to a task — prepares worktree and links them."""
    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile
    from planet_maiko.agents.coding_agent import prepare

    data = request.get_json()
    task_id = data.get("task_id")
    profile_id = data.get("profile_id")
    repo_path = data.get("repo_path", "")
    use_worktree = data.get("use_worktree", True)
    auto_kickoff = data.get("auto_kickoff", False)
    branch_name = data.get("branch_name")

    if not task_id or not profile_id:
        return jsonify({"error": "task_id and profile_id are required"}), 400

    if not repo_path:
        return jsonify({"error": "repo_path is required. Select a repo in the assign modal."}), 400

    task = db.get_or_404(Task, task_id)
    profile = db.get_or_404(AgentProfile, profile_id)

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
            auto_kickoff=auto_kickoff,
            use_worktree=use_worktree,
            agent_profile_id=profile_id,
        )
    except Exception as e:
        return jsonify({"error": f"Agent preparation failed: {str(e)}"}), 500

    if not result:
        return jsonify({"error": "Failed to prepare agent"}), 500

    # Link task to agent
    task.assigned_agent_id = profile_id
    if task.status == "new":
        task.status = "in_progress"
    db.session.commit()

    return jsonify({
        "task": task.to_dict(),
        "agent": profile.to_dict(),
        "worktree": result,
    }), 201


@agents_bp.route("/agents/resume-session", methods=["POST"])
def resume_session():
    """Open a terminal that resumes an agent's Claude Code session."""
    import subprocess
    import sys

    data = request.get_json()
    task_id = data.get("task_id", "")
    session_id = _agent_sessions.get(task_id)

    if not session_id:
        return jsonify({"error": "No session ID found for this agent. The agent may not have started yet."}), 404

    cmd = f"claude --resume {session_id}"

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Terminal.app", "--args", "-e", cmd])
            # Terminal.app doesn't support --args well, use osascript instead
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
        return jsonify({"status": "opened", "session_id": session_id, "command": cmd})
    except Exception as e:
        return jsonify({"error": str(e), "command": cmd}), 500


@agents_bp.route("/agents/open-terminal", methods=["POST"])
def open_terminal():
    """Open a terminal — attach to tmux session if running, or start fresh."""
    import subprocess
    import sys
    import shutil

    data = request.get_json()
    path = data.get("path", "")
    task_id = data.get("task_id", "")

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
        # Attach to existing tmux session in a new terminal
        attach_cmd = f"tmux attach -t {session_name}"
    else:
        # Start fresh with claude
        initial_prompt = "Read TASK.md and CLAUDE.md in this directory. Begin working on the task following the protocol."
        attach_cmd = f'cd {path} && claude "{initial_prompt}"'

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
        return jsonify({"status": "opened", "path": path, "tmux_attached": has_tmux_session})
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
            "eod-summary": f"EOD Summary — {datetime.now().strftime('%B %d')}",
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
    """Get messages for an agent (agent polls this).

    Query params:
        unread_only: "true" to only return unread messages (default: true)
        mark_read: "true" to auto-mark returned messages as read (default: true)
    """
    unread_only = request.args.get("unread_only", "true").lower() == "true"
    mark_read = request.args.get("mark_read", "true").lower() == "true"

    query = AgentMessage.query.filter_by(task_id=task_id, direction="to_agent")
    if unread_only:
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

    # If feedback message, create a learning signal immediately
    if data.get("message_type") == "feedback":
        metadata = data.get("metadata", {})
        category = metadata.get("feedback_category", "pattern")
        severity = metadata.get("feedback_severity", "suggestion")

        from planet_maiko.models.signal import Signal
        signal = Signal(
            category=category,
            text=data["content"],
            source_type="session_feedback",
            severity=severity,
            repo=_get_repo_for_task(task_id),
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


# In-memory session ID store (task_id -> session_id)
_agent_sessions = {}


@agents_bp.route("/agents/<task_id>/session", methods=["POST"])
def register_session(task_id):
    """Register a Claude Code session ID for an agent task."""
    data = request.get_json()
    session_id = data.get("session_id")
    if session_id:
        _agent_sessions[task_id] = session_id
    return jsonify({"status": "ok"})


@agents_bp.route("/agents/<task_id>/session", methods=["GET"])
def get_session(task_id):
    """Get the Claude Code session ID for an agent task."""
    return jsonify({"session_id": _agent_sessions.get(task_id)})


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
        id=f"hook-{agent_id}-{event}-{int(datetime.now(timezone.utc).timestamp())}",
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
        id=f"hook-notify-{agent_id}-{int(datetime.now(timezone.utc).timestamp())}",
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
    """Handle subagent-stop hook: create low-priority pupdate."""
    from datetime import datetime, timezone
    from planet_maiko.models.pupdate import Pupdate

    data = request.get_json()
    task_id = data.get("task_id", "")
    agent_id = data.get("agent_id", "")

    pupdate = Pupdate(
        id=f"hook-subagent-{agent_id}-{int(datetime.now(timezone.utc).timestamp())}",
        source="agent",
        source_id=f"agent/{agent_id}",
        type="agent_update",
        priority="low",
        title="Subagent finished",
        body=f"A subagent for {agent_id} completed its work.",
        tags=[task_id, "agent", "subagent"],
        extra={
            "agent_id": agent_id,
            "task_id": task_id,
        },
    )
    db.session.add(pupdate)
    db.session.commit()
    return jsonify({"status": "ok"}), 201
