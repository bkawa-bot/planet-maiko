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

    result = prepare(
        task_id=task_id,
        task_title=task.title,
        prompt=full_prompt,
        repo_path=repo_path,
        branch_prefix="maiko",
        auto_kickoff=auto_kickoff,
        use_worktree=use_worktree,
    )

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


@agents_bp.route("/agents/open-terminal", methods=["POST"])
def open_terminal():
    """Open a terminal window at the given path."""
    import subprocess
    import sys

    data = request.get_json()
    path = data.get("path", "")

    if not path or not os.path.isdir(path):
        return jsonify({"error": "Invalid path"}), 400

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Terminal", path])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", f"cd /d {path}"], shell=True)
        else:
            # Linux — try common terminal emulators
            for term in ["gnome-terminal", "xterm", "konsole", "xfce4-terminal"]:
                try:
                    subprocess.Popen([term, "--working-directory", path])
                    break
                except FileNotFoundError:
                    continue
        return jsonify({"status": "opened", "path": path})
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
