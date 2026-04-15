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
import re
import subprocess
import uuid
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


def _install_pre_commit_hook(working_path):
    """Install git pre-commit hook that runs LoRA compliance review."""
    import shutil
    import stat

    hooks_src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))), "hooks", "pre_commit_review.py")

    if not os.path.exists(hooks_src):
        return

    # Find the git hooks directory for this worktree
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, cwd=working_path, timeout=5,
        )
        git_dir = result.stdout.strip()
        if not os.path.isabs(git_dir):
            git_dir = os.path.join(working_path, git_dir)
        hooks_dir = os.path.join(git_dir, "hooks")
        os.makedirs(hooks_dir, exist_ok=True)

        hook_path = os.path.join(hooks_dir, "pre-commit")
        # Write a wrapper that calls our review script
        with open(hook_path, "w", encoding="utf-8") as f:
            f.write(f"#!/bin/sh\npython3 {hooks_src}\n")
        os.chmod(hook_path, os.stat(hook_path).st_mode | stat.S_IEXEC)

        logger.info(f"[agent] Installed pre-commit review hook in {hooks_dir}")
    except Exception as e:
        logger.debug(f"[agent] Could not install pre-commit hook: {e}")


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


def _write_claude_md(working_path, task_id, task_title, role="coding", maiko_port=None):
    """Write CLAUDE.md with full agent protocol.

    Loads the protocol template for the given role. "coding" uses the
    full agent-protocol (MCP channel, task-state reporting, etc.);
    review/investigation use their own protocol prompts which describe
    the structured-block output contract their initial one-shot run
    follows.
    """
    if maiko_port is None:
        from planet_maiko.config import MAIKO_PORT
        maiko_port = MAIKO_PORT

    custom_instructions = ""
    role_instructions_for_role = ""
    try:
        from planet_maiko.config import load_config
        agents_cfg = load_config().get("agents", {}) or {}
        custom_instructions = agents_cfg.get("custom_instructions", "") or ""
        role_instructions_for_role = (agents_cfg.get("role_instructions") or {}).get(role, "") or ""
    except Exception:
        pass

    protocol_skill = {
        "review": "review-agent-protocol",
        "investigation": "investigation-agent-protocol",
    }.get(role, "agent-protocol")

    # Load protocol from skill prompt (editable via Skills page)
    content = None
    try:
        from planet_maiko.agents.skills import get_skill_prompt
        content = get_skill_prompt(protocol_skill, {
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
            "prompts", f"{protocol_skill}.md"
        )
        try:
            with open(prompt_path, "r") as f:
                content = f.read()
            content = content.replace("{task_title}", task_title)
            content = content.replace("{task_id}", task_id)
            content = content.replace("{maiko_port}", str(maiko_port))
        except Exception:
            content = f"# Agent Protocol\n\nTask: {task_title}\nTask ID: {task_id}\nRole: {role}\n\nRead TASK.md for instructions."

    if role_instructions_for_role:
        content += f"\n\n## Team instructions for {role} agents\n\n{role_instructions_for_role.strip()}\n"
    if custom_instructions and role == "coding":
        content += f"\n\n## Owner's Workflow Preferences\n\n{custom_instructions}\n"

    claude_dir = os.path.join(working_path, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    with open(os.path.join(working_path, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write(content)


def _write_mcp_json(working_path, task_id):
    """Write .mcp.json so the maiko-channel auto-loads when claude starts."""
    import json

    # Find the channel script path relative to the planet-maiko repo root
    # __file__ is src/planet_maiko/agents/coding_agent.py — go up 4 levels to repo root
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))
    channel_path = os.path.join(repo_root, "channel", "index.mjs")

    # Fall back to looking relative to the working path
    if not os.path.exists(channel_path):
        channel_path = os.path.join(working_path, "..", "..", "channel", "index.mjs")

    from planet_maiko.config import maiko_api_url
    mcp_config = {
        "mcpServers": {
            "maiko-channel": {
                "command": "node",
                "args": [channel_path],
                "env": {
                    "MAIKO_TASK_ID": task_id,
                    "MAIKO_API_URL": maiko_api_url(),
                    "MAIKO_POLL_MS": "60000",
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

    # Resolve hooks directory — same repo root as _write_mcp_json
    hooks_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))), "hooks")

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
        hooks["PostToolUse"] = [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/post_tool_use.py"}],
            },
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/lora_review_hook.py"}],
            },
        ]

    if hooks_config.get("post_compact", True):
        hooks["PostCompact"] = [{
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/post_compact.py"}],
        }]

    if hooks_config.get("notification", True):
        hooks["Notification"] = [{
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/notification.py"}],
        }]

    if hooks_config.get("subagent_stop", True):
        hooks["SubagentStop"] = [{
            "matcher": "*",
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
    from planet_maiko.config import maiko_api_url
    env_data = {
        "task_id": task_id,
        "agent_id": agent_id,
        "api_url": maiko_api_url(),
    }
    with open(os.path.join(working_path, ".maiko-env.json"), "w") as f:
        json.dump(env_data, f, indent=2)

    logger.info(f"[agent] Wrote Claude hooks settings for {agent_id} ({len(hooks)} hooks)")


# Branch names: git already forbids most metacharacters but defense in depth.
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
# Path validation: catch shell metacharacters but allow real path chars
# (letters, digits, spaces, dot, dash, underscore, slash, backslash, colon, paren).
_UNSAFE_PATH_CHARS = re.compile(r'[;&|`$<>!"*?\n\r]')


def _kickoff_agent_headless(agent_id, worktree_path, task_id, branch_name=None, plan_first=False):
    """Start the coding agent as a background daemon thread — no terminal.

    Mirrors the review / investigation autonomous flow: `claude --print`
    runs in the worktree, tees its transcript to agent.log, and exits
    when it's reached a stopping point (in the coding case, sent a
    ready_for_review message via MCP). The session id is registered
    so "View Session" can later `claude --resume` into it for the
    user to iterate alongside the agent.

    plan_first=True starts the run in Claude's plan mode and prompts
    the agent to produce a markdown plan first — it calls
    reply(message_type="plan_for_approval") and exits without writing
    code. The user approves or requests changes in the plan UI; the
    approve endpoint resumes the session without plan mode so the
    agent can actually implement.

    Returns immediately after spawning the thread.
    """
    import shutil
    import threading

    claude_path = shutil.which("claude")
    if not claude_path:
        return {"success": False, "error": "claude CLI not found"}
    if branch_name and not _SAFE_BRANCH_RE.match(branch_name):
        return {"success": False, "error": f"Unsafe branch name: {branch_name!r}"}
    if _UNSAFE_PATH_CHARS.search(worktree_path):
        return {"success": False, "error": f"Unsafe worktree path: {worktree_path!r}"}

    session_id = str(uuid.uuid4())
    from planet_maiko.api.agents_api import _set_session
    _set_session(task_id, session_id, worktree_path)

    if plan_first:
        initial_prompt = (
            "Read TASK.md and CLAUDE.md in this directory. Do NOT write "
            "any code yet. Produce a detailed implementation plan "
            "(markdown, 500 words max) covering: what you'll change, in "
            "which files, in what order, and the key decisions / risks. "
            "When the plan is ready, call reply(message_type=\"plan_for_approval\", "
            "content=<your plan>) via the maiko-channel MCP and exit. "
            "The user will approve or request changes; Maiko will resume "
            "you with their decision."
        )
    else:
        initial_prompt = (
            "Read TASK.md and CLAUDE.md in this directory. Begin working on the "
            "task following the protocol. After your first meaningful commit, "
            "call reply(message_type=\"ready_for_review\", content=\"...\") via "
            "the maiko-channel MCP and then use check_inbox to receive any "
            "review feedback. Do not git push or open PRs — Maiko handles that "
            "once the human approves."
        )

    # No --allowedTools alongside --dangerously-skip-permissions — the
    # skip flag is the blanket bypass, and passing allowlists on top
    # gets treated as restrictive scope filter that stalls on writes
    # to unlisted paths and MCP subtools. Worktree isolation makes the
    # nuclear option safe here.
    cmd = [
        claude_path, "--print", "--output-format", "text",
        "--session-id", session_id,
        "--dangerously-skip-permissions",
    ]
    if plan_first:
        # Claude's plan mode restricts the tool set to read-only
        # (Read/Glob/Grep/etc.), so the agent can't write even if its
        # prompt discipline slips. Reply via MCP still works since
        # MCP tools aren't disk-modifying.
        cmd.extend(["--permission-mode", "plan"])

    log_path = os.path.join(worktree_path, "agent.log")

    def _run():
        try:
            with open(log_path, "w", encoding="utf-8") as log:
                log.write(f"# Headless coding agent run\n# session_id: {session_id}\n\n")
                log.flush()
                subprocess.run(
                    cmd,
                    input=initial_prompt,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=worktree_path,
                )
        except Exception as e:
            logger.warning(f"[agent] Headless run for {task_id} failed: {e}")

    threading.Thread(target=_run, daemon=True, name=f"coding-{task_id}").start()
    logger.info(f"[agent] Headless coding agent launched for {agent_id} (session {session_id[:8]})")
    return {
        "success": True,
        "working_path": worktree_path,
        "session_id": session_id,
        "mode": "headless",
        "log_path": log_path,
    }


def _kickoff_agent(agent_id, worktree_path, task_id, branch_name=None):
    """Start the agent in a detached tmux session. View with 'View Session'."""
    import shutil
    import sys

    claude_path = shutil.which("claude")
    if not claude_path:
        return {"success": False, "error": "claude CLI not found"}

    # branch_name and worktree_path are interpolated into shell commands across
    # tmux/osascript/cmd/bash launchers below — validate to prevent injection
    # from a hostile branch name like `main; rm -rf ~`.
    if branch_name and not _SAFE_BRANCH_RE.match(branch_name):
        return {"success": False, "error": f"Unsafe branch name: {branch_name!r}"}
    if _UNSAFE_PATH_CHARS.search(worktree_path):
        return {"success": False, "error": f"Unsafe worktree path: {worktree_path!r}"}

    tmux_path = shutil.which("tmux")
    initial_prompt = "Read TASK.md and CLAUDE.md in this directory. Begin working on the task following the protocol. Report your status as you go."
    session_name = f"maiko-{task_id}"

    # Generate a session ID upfront so we can resume later via "View Session"
    session_id = str(uuid.uuid4())
    from planet_maiko.api.agents_api import _set_session
    _set_session(task_id, session_id, worktree_path)

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
    launch_cmd = f'{checkout}cd {worktree_path} && claude --session-id {session_id} {tools_flags} "{initial_prompt}"'

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
            auto_kickoff=False, use_worktree=True, agent_profile_id=None,
            role="coding"):
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
        role: "coding" | "review" | "investigation" — picks the CLAUDE.md
            protocol template and role-scoped team instructions.

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

    # If the user typed a full branch name (contains /), use it as-is.
    # Otherwise treat it as a prefix and append the auto-generated slug.
    if "/" in branch_prefix:
        branch_name = branch_prefix
    else:
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
    _write_claude_md(working_path, task_id, task_title, role=role)
    _write_mcp_json(working_path, task_id)

    # Use existing profile if provided, otherwise generate an ID
    agent_id = agent_profile_id or f"agent-{branch_name}"

    # Write Claude Code hooks configuration
    _write_claude_settings(working_path, task_id, agent_id)

    # LoRA compliance review is now handled by Claude Code PostToolUse hook
    # (lora_review_hook.py), registered in _write_claude_settings above.

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
    # Review/investigation agents run autonomously after prepare, so the
    # "ready to launch" framing doesn't fit — their action is "dig deeper"
    # once the result pupdate arrives. Coding agents still need a manual
    # terminal launch.
    if role == "coding":
        ready_title = f"Agent ready: {task_title}"
        ready_hint = "Launch agent"
    else:
        ready_title = f"{role.capitalize()} agent working: {task_title}"
        ready_hint = "Dig deeper"
    notify = Pupdate(
        id=f"agent-ready-{task_id}-{uuid.uuid4().hex[:8]}",
        source="maiko",
        source_id=f"agent/{agent_id}",
        type="agent_ready",
        priority="normal",
        title=ready_title,
        body=f"Prepared on branch `{branch_name}` ({mode}).\n\n{'Working in: ' + working_path if use_worktree else 'Checkout: git checkout ' + branch_name}",
        actionable=True,
        action_hint=ready_hint,
        tags=[task_id, "agent", role],
        extra={
            "agent_id": agent_id,
            "branch": branch_name,
            "working_path": working_path,
            "mode": mode,
            "task_id": task_id,
            "role": role,
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
    """List prepared agent worktrees whose task is still open.

    Filters out agent_ready pupdates that are dismissed, that
    reference a task in done/cancelled state, or that reference a
    task that no longer exists. Those are finished work, not active
    work — leaving them on the Active tab buries the real signal.
    """
    from planet_maiko.models.task import Task
    agents = Pupdate.query.filter_by(type="agent_ready", dismissed=False).all()
    task_ids = [p.extra.get("task_id") for p in agents if p.extra.get("task_id")]
    tasks_by_id = {}
    if task_ids:
        tasks_by_id = {t.id: t for t in Task.query.filter(Task.id.in_(task_ids)).all()}
    out = []
    for p in agents:
        tid = p.extra.get("task_id")
        task = tasks_by_id.get(tid)
        if tid and not task:
            continue  # task deleted
        if task and task.status in ("done", "cancelled"):
            continue  # finished
        out.append({
            "agent_id": p.extra.get("agent_id"),
            "task_id": tid,
            "task_title": task.title if task else None,
            "task_status": task.status if task else None,
            "role": p.extra.get("role", "coding"),
            "branch": p.extra.get("branch"),
            "working_path": p.extra.get("working_path"),
            "prepared_at": p.timestamp.isoformat() if p.timestamp else None,
        })
    return out
