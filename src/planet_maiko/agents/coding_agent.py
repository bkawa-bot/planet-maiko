"""Coding agent orchestrator - prepares everything an agent needs to work.

Planet Maiko doesn't control the agent runtime. It controls the
surrounding orchestration:

    1. Prepare - create worktree, write TASK.md and CLAUDE.md
    2. Notify - tell the user an agent task is ready to launch
    3. Monitor - watch for agent pupdates via the API
    4. Collect - when agent reports done, update task status

The agent (Claude Code, Aider, whatever) communicates back by calling
the Planet Maiko API, either directly or through the `maiko` CLI:

    maiko report "Finished implementing OAuth"
    maiko task done
"""

import logging
import os
import subprocess
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate

logger = logging.getLogger(__name__)


def _slugify(text, max_len=40):
    slug = text.lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = "-".join(slug.split())
    return slug[:max_len]


def _create_worktree(repo_path, branch_name):
    """Create a git worktree for an agent to work in."""
    worktree_base = os.path.join(repo_path, ".maiko-worktrees")
    os.makedirs(worktree_base, exist_ok=True)
    worktree_path = os.path.join(worktree_base, branch_name)

    try:
        subprocess.run(
            ["git", "branch", branch_name],
            cwd=repo_path, capture_output=True, text=True,
        )
        result = subprocess.run(
            ["git", "worktree", "add", worktree_path, branch_name],
            cwd=repo_path, capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.error(f"Failed to create worktree: {result.stderr}")
            return None
        return worktree_path
    except Exception as e:
        logger.error(f"Worktree creation failed: {e}")
        return None


def _write_task_file(working_path, task_id, task_title, prompt):
    """Write TASK.md so the agent knows what to do."""
    content = f"""# Task: {task_title}

**Task ID:** {task_id}
**Created:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

## Instructions

{prompt}
"""
    with open(os.path.join(working_path, "TASK.md"), "w", encoding="utf-8") as f:
        f.write(content)


def _write_claude_md(working_path, task_id, task_title, maiko_port=8420):
    """Write CLAUDE.md with full agent protocol.

    Loads the protocol template from the agent-protocol skill prompt file,
    which can be customized from the Skills page.
    """
    custom_instructions = ""
    try:
        from planet_maiko.config import load_config
        config = load_config()
        custom_instructions = config.get("agents", {}).get("custom_instructions", "")
    except Exception:
        pass

    # Load protocol from skill prompt (editable via Skills page)
    content = None
    try:
        from planet_maiko.agents.skills import get_skill_prompt
        content = get_skill_prompt("agent-protocol", {
            "task_title": task_title,
            "task_id": task_id,
            "maiko_port": str(maiko_port),
        })
    except Exception:
        pass

    # Fallback to the prompt file directly
    if not content:
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "prompts", "agent-protocol.md"
        )
        try:
            with open(prompt_path, "r") as f:
                content = f.read()
            content = content.replace("{task_title}", task_title)
            content = content.replace("{task_id}", task_id)
            content = content.replace("{maiko_port}", str(maiko_port))
        except Exception:
            content = f"# Agent Protocol\n\nTask: {task_title}\nTask ID: {task_id}\n\nRead TASK.md for instructions."

    if custom_instructions:
        content += f"\n\n## Owner's Workflow Preferences\n\n{custom_instructions}\n"

    claude_dir = os.path.join(working_path, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    with open(os.path.join(working_path, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write(content)


def _write_mcp_json(working_path, task_id):
    """Write .mcp.json so the maiko-channel auto-loads when claude starts."""
    import json

    # Find the channel script path relative to the planet-maiko install
    channel_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))), "channel", "index.js")

    # Fall back to looking relative to the working path
    if not os.path.exists(channel_path):
        channel_path = os.path.join(working_path, "..", "..", "channel", "index.js")

    mcp_config = {
        "mcpServers": {
            "maiko-channel": {
                "command": "node",
                "args": [channel_path],
                "env": {
                    "MAIKO_TASK_ID": task_id,
                    "MAIKO_API_URL": "http://localhost:8420/api",
                    "MAIKO_POLL_MS": "5000",
                },
            }
        }
    }

    with open(os.path.join(working_path, ".mcp.json"), "w") as f:
        json.dump(mcp_config, f, indent=2)


def _write_claude_settings(working_path, task_id, agent_id):
    """Write .claude/settings.json (hooks config) and .maiko-env.json (identity).

    Checks the hooks config to determine which hooks are enabled before
    including them in settings.json.
    """
    import json

    # Resolve hooks directory (same pattern as _write_mcp_json)
    hooks_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))), "hooks")

    # Fall back to looking relative to the working path
    if not os.path.isdir(hooks_dir):
        hooks_dir = os.path.join(working_path, "..", "..", "hooks")

    # Normalize to absolute path with forward slashes for cross-platform compat
    hooks_dir = os.path.abspath(hooks_dir)

    # Load hooks config
    try:
        from planet_maiko.config import load_config
        config = load_config()
        hooks_config = config.get("hooks", {})
    except Exception:
        hooks_config = {"enabled": True}

    if not hooks_config.get("enabled", True):
        return

    # Build hooks dict, only including enabled hooks
    hooks = {}

    if hooks_config.get("post_tool_use", True):
        hooks["PostToolUse"] = [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/post_tool_use.py"}],
        }]

    if hooks_config.get("post_compact", True):
        hooks["PostCompact"] = [{
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/post_compact.py"}],
        }]

    if hooks_config.get("notification", True):
        hooks["Notification"] = [{
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/notification.py"}],
        }]

    if hooks_config.get("subagent_stop", True):
        hooks["SubagentStop"] = [{
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/subagent_stop.py"}],
        }]

    if not hooks:
        return

    settings = {"hooks": hooks}

    # Write .claude/settings.json
    claude_dir = os.path.join(working_path, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    with open(os.path.join(claude_dir, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2)

    # Write .maiko-env.json for hook scripts to read
    env_data = {
        "task_id": task_id,
        "agent_id": agent_id,
        "api_url": "http://localhost:8420/api",
    }
    with open(os.path.join(working_path, ".maiko-env.json"), "w") as f:
        json.dump(env_data, f, indent=2)

    logger.info(f"[agent] Wrote Claude hooks settings for {agent_id} ({len(hooks)} hooks)")


def _kickoff_agent(agent_id, worktree_path, task_id, branch_name=None):
    """Start the agent in a detached tmux session. View with 'View Session'."""
    import shutil
    import sys

    claude_path = shutil.which("claude")
    if not claude_path:
        return {"success": False, "error": "claude CLI not found"}

    tmux_path = shutil.which("tmux")
    initial_prompt = "Read TASK.md and CLAUDE.md in this directory. Begin working on the task following the protocol. Report your status as you go."
    session_name = f"maiko-{task_id}"

    # Pre-approve the MCP channel + user's configured tools
    allowed_tools = ["mcp__maiko-channel"]
    try:
        from planet_maiko.config import load_config
        user_tools = load_config().get("brain", {}).get("allowed_tools", [])
        allowed_tools.extend(user_tools)
    except Exception:
        pass
    tools_flags = " ".join(f'--allowedTools "{t}"' for t in allowed_tools)

    # Build the launch command — checkout branch first if needed
    checkout = f"git checkout {branch_name} && " if branch_name else ""
    launch_cmd = f'{checkout}cd {worktree_path} && claude {tools_flags} "{initial_prompt}"'

    try:
        if tmux_path:
            subprocess.Popen([
                tmux_path, "new-session", "-d", "-s", session_name,
                "-c", worktree_path,
                "bash", "-c", launch_cmd,
            ])
            logger.info(f"[agent] Launched in tmux session '{session_name}' for {agent_id}")
            return {"success": True, "working_path": worktree_path, "tmux_session": session_name}
        else:
            if sys.platform == "darwin":
                subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{launch_cmd}"'])
            elif sys.platform == "win32":
                subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", launch_cmd], shell=True)
            else:
                for term in ["gnome-terminal", "xterm", "konsole"]:
                    try:
                        subprocess.Popen([term, "--", "bash", "-c", launch_cmd])
                        break
                    except FileNotFoundError:
                        continue
            logger.info(f"[agent] Launched in terminal for {agent_id}")
            return {"success": True, "working_path": worktree_path}
    except Exception as e:
        logger.error(f"[agent] Kickoff failed for {agent_id}: {e}")
        return {"success": False, "error": str(e)}


def _create_branch_only(repo_path, branch_name):
    """Create a branch for agent work, then switch back to the original branch.

    The task files get committed to the new branch, but the repo is left
    on its original branch so it doesn't block the user's workflow.
    """
    try:
        # Remember current branch
        current = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path, capture_output=True, text=True,
        ).stdout.strip()

        result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=repo_path, capture_output=True, text=True,
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["git", "checkout", branch_name],
                cwd=repo_path, capture_output=True, text=True,
            )
            if result.returncode != 0:
                logger.error(f"[agent] Could not create or checkout branch {branch_name}")
                return None

        # Store original branch so we can switch back after file writing
        _branch_return_to[repo_path] = current
        return repo_path
    except Exception as e:
        logger.error(f"[agent] Failed to create branch {branch_name}: {e}")
        return None


# Track which branch to return to after preparing files
_branch_return_to = {}


def _finalize_branch(repo_path):
    """Commit task files and switch back to the original branch."""
    original = _branch_return_to.pop(repo_path, None)
    if not original:
        return
    try:
        subprocess.run(
            ["git", "add", "-f", "TASK.md", "CLAUDE.md", ".mcp.json", ".maiko-env.json", ".claude/"],
            cwd=repo_path, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Maiko: prepare agent task files", "--no-verify"],
            cwd=repo_path, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "checkout", original],
            cwd=repo_path, capture_output=True, text=True,
        )
        logger.info(f"[agent] Task files committed, switched back to {original}")
    except Exception as e:
        logger.warning(f"[agent] Could not switch back to {original}: {e}")


def prepare(task_id, task_title, prompt, repo_path, branch_prefix="maiko",
            auto_kickoff=False, use_worktree=True, agent_profile_id=None):
    """Prepare for an agent to work on a task.

    Two modes:
    - use_worktree=True (default): creates a git worktree on a new branch.
      Agent works in an isolated directory.
    - use_worktree=False: creates a branch in the main repo. Agent works
      in the repo directory directly (simpler but not isolated).

    If auto_kickoff=True, the agent is started via the configured runtime
    after preparation.

    Args:
        task_id: the task this agent will work on
        task_title: human-readable task title
        prompt: full instructions for the agent
        repo_path: path to the git repository
        branch_prefix: prefix for the branch name
        auto_kickoff: if True, immediately start the agent via the runtime
        use_worktree: if True, create a git worktree; if False, just create a branch

    Returns:
        dict with agent info and launch instructions, or None on failure
    """
    # Build a descriptive branch name from the task title
    import time as _time
    slug = _slugify(task_title, max_len=40)
    if not slug:
        slug = _slugify(task_id)
    # Add short timestamp to avoid collisions
    slug = f"{slug}-{str(int(_time.time()))[-4:]}"

    # Use configured prefix, or the one passed in (from custom branch name field)
    if branch_prefix == "maiko":
        try:
            from planet_maiko.config import load_config
            cfg_prefix = load_config().get("agents", {}).get("branch_prefix", "maiko")
            if cfg_prefix:
                branch_prefix = cfg_prefix
        except Exception:
            pass

    branch_name = f"{branch_prefix}/{slug}"

    if use_worktree:
        working_path = _create_worktree(repo_path, branch_name)
    else:
        working_path = _create_branch_only(repo_path, branch_name)

    if not working_path:
        return None

    # Write task files
    _write_task_file(working_path, task_id, task_title, prompt)
    _write_claude_md(working_path, task_id, task_title)
    _write_mcp_json(working_path, task_id)

    # Use existing profile if provided, otherwise generate an ID
    agent_id = agent_profile_id or f"agent-{branch_name}"

    # Write Claude Code hooks configuration
    _write_claude_settings(working_path, task_id, agent_id)

    # Compile learning brief for this agent
    try:
        from planet_maiko.brain.learning.processor import compile_brief
        from planet_maiko.agents.profiles import create_profile

        # Only create a profile if one wasn't provided
        if not agent_profile_id:
            create_profile(agent_id)
        brief = compile_brief(
            repo=repo_path,
            task_id=task_id,
            agent_profile_id=agent_id,
        )

        if brief and brief != "No active learnings yet.":
            claude_path = os.path.join(working_path, "CLAUDE.md")
            with open(claude_path, "a") as f:
                f.write("\n\n" + brief)
            logger.info(f"[agent] Injected learning brief ({len(brief)} chars) for {agent_id}")
    except Exception as e:
        logger.warning(f"[agent] Could not compile brief for {agent_id}: {e}")

    # If branch-only mode, commit task files and switch back to original branch
    if not use_worktree:
        _finalize_branch(working_path)

    mode = "worktree" if use_worktree else "branch"
    notify = Pupdate(
        id=f"agent-ready-{task_id}-{int(datetime.now(timezone.utc).timestamp())}",
        source="maiko",
        source_id=f"agent/{agent_id}",
        type="agent_ready",
        priority="normal",
        title=f"Agent ready: {task_title}",
        body=f"Prepared on branch `{branch_name}` ({mode}).\n\n{'Launch in: ' + working_path if use_worktree else 'Checkout: git checkout ' + branch_name}",
        actionable=True,
        action_hint="Launch agent",
        tags=[task_id, "agent"],
        extra={
            "agent_id": agent_id,
            "branch": branch_name,
            "working_path": working_path,
            "mode": mode,
            "task_id": task_id,
        },
    )
    db.session.add(notify)
    db.session.commit()

    logger.info(f"[orchestrator] Prepared agent {agent_id} at {working_path} ({mode})")

    result = {
        "agent_id": agent_id,
        "task_id": task_id,
        "branch": branch_name,
        "working_path": working_path,
        "mode": mode,
        "status": "ready",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "launch_instructions": {
            "claude_code": f'cd {working_path} && claude "Read TASK.md and CLAUDE.md. Begin working on the task."',
            "aider": f"cd {working_path} && aider",
            "manual": f"cd {working_path} && cat TASK.md",
        },
    }

    if auto_kickoff:
        kickoff_result = _kickoff_agent(agent_id, working_path, task_id, branch_name=branch_name if not use_worktree else None)
        result["status"] = "running" if kickoff_result.get("success") else "ready"
        result["kickoff_result"] = kickoff_result

    return result


def cleanup(repo_path, branch_name):
    """Remove a worktree and its branch after agent is done."""
    worktree_path = os.path.join(repo_path, ".maiko-worktrees", branch_name)
    try:
        subprocess.run(
            ["git", "worktree", "remove", worktree_path, "--force"],
            cwd=repo_path, capture_output=True, text=True,
        )
    except Exception as e:
        logger.warning(f"Worktree cleanup failed: {e}")


def list_prepared():
    """List all prepared agent worktrees by checking for agent_ready pupdates."""
    agents = Pupdate.query.filter_by(type="agent_ready", dismissed=False).all()
    return [
        {
            "agent_id": p.extra.get("agent_id"),
            "task_id": p.extra.get("task_id"),
            "branch": p.extra.get("branch"),
            "working_path": p.extra.get("working_path"),
            "prepared_at": p.timestamp.isoformat() if p.timestamp else None,
        }
        for p in agents
    ]
