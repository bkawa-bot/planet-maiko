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
    """Write CLAUDE.md with full agent protocol."""
    custom_instructions = ""
    try:
        from planet_maiko.config import load_config
        config = load_config()
        custom_instructions = config.get("agents", {}).get("custom_instructions", "")
    except Exception:
        pass

    branch_var = "$(git rev-parse --abbrev-ref HEAD)"

    content = f"""# Planet Maiko — Agent Protocol

You are a coding agent managed by Planet Maiko. Read TASK.md for your assignment.

**Task:** {task_title}
**Task ID:** {task_id}

## 0. First Steps

1. Read **TASK.md** in this directory — it has your full instructions.
2. Get your branch name: `BRANCH={branch_var}`
3. Announce yourself:
```bash
maiko report "Starting work on {task_id}. Reading plan and exploring codebase."
maiko task start
```

## 1. Communication

All communication goes through the `maiko` CLI (connects to http://localhost:{maiko_port}).

### Commands

| Command | When to use |
|---------|-------------|
| `maiko report "message"` | After every major step — keeps your status fresh |
| `maiko inbox` | After each commit or before starting a new subtask |
| `maiko reply "message"` | When responding to a message from Maiko or the user |
| `maiko feedback "message" --category testing` | When you discover something that should become a learning |
| `maiko task done` | When the task is complete and tests pass |
| `maiko task stuck -m "description"` | When you're blocked and need help |

### Status Update Convention

**Your report messages appear as speech bubbles on the dashboard.** Write them like you'd talk to the user if they walked by your desk — conversational, first person, one sentence.

Good: "Tests passing, pushing to remote now!"
Bad: "agent_status: build complete for task-123"

### When to Report

Send a `maiko report` after every major workflow step:
- After reading the plan and exploring the codebase
- After implementing a significant piece
- After each build attempt (pass or fail)
- After committing and pushing
- After opening a PR
- When blocked or waiting

**Do NOT sit idle without reporting.** If you're blocked, say so immediately via `maiko task stuck`.

## 2. Workflow

```
1. Read TASK.md → report "Reading the plan..."
2. Explore codebase → report "Exploring the codebase and checking existing patterns."
3. Implement changes → report "Implementing changes to X..."
4. Run tests/build → report "Tests passing!" or "Build failed, fixing..."
5. Commit & push → report "Changes pushed to branch."
6. Open draft PR → report "Draft PR #N opened."
7. Self-review the diff → report "Self-reviewing the diff..."
8. Fix any issues found → commit & push
9. Report "PR #{task_id} ready for review."
10. maiko task done
```

## 3. Checking for Messages

**Check `maiko inbox` after each commit or between subtasks.** Maiko may send:
- Updated context or changed requirements
- Answers to questions you asked
- A nudge if you haven't reported in a while
- A sleep signal (stop work and wait)

If you receive a nudge, immediately report your current status.

## 4. Post-Review Feedback

After the user reviews your work and requests changes, extract learnings:

For EACH specific pattern change the reviewer requested, send feedback:
```bash
maiko feedback "Use orElseThrow instead of .get() on Optional" --category error_handling
```

This feeds Maiko's learning system so future agents get better coding guidelines.
Send one feedback per distinct code pattern (not per file — if the same pattern was applied in 3 files, that's 1 feedback).

## 5. Rules

- Stay focused on the task in TASK.md
- Commit frequently with clear, descriptive messages
- **Check `maiko inbox` after every commit** — Maiko may have new context
- Match existing patterns in the files you're modifying
- If stuck for more than a few minutes, report it — don't spin
- When done, verify tests pass before reporting completion
- NEVER commit agent scaffolding files (TASK.md, CLAUDE.md, .claude/ plans)
"""

    if custom_instructions:
        content += f"""
## 6. Owner's Workflow Preferences

{custom_instructions}
"""
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


def _kickoff_agent(agent_id, worktree_path, task_id):
    """Start the agent via the configured runtime."""
    try:
        from planet_maiko.agents.brain_session import BrainSession
        session = BrainSession()
        if not session.runtime or not session.runtime.is_available():
            return {"success": False, "error": "Runtime not available"}

        # Read the task context
        task_path = os.path.join(worktree_path, "TASK.md")
        with open(task_path, "r") as f:
            task_content = f.read()

        result = session.runtime.send(
            f"Work on this task in the current directory:\n\n{task_content}",
            working_dir=worktree_path,
            timeout=3600,
        )
        return {"success": True, "output": result[:500] if result else ""}
    except Exception as e:
        logger.error(f"[agent] Kickoff failed for {agent_id}: {e}")
        return {"success": False, "error": str(e)}


def _create_branch_only(repo_path, branch_name):
    """Create a branch without a worktree — agent works in the main repo."""
    try:
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=repo_path, capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Branch might already exist
            subprocess.run(
                ["git", "checkout", branch_name],
                cwd=repo_path, capture_output=True, text=True,
            )
        return repo_path  # Working dir is the repo itself
    except Exception as e:
        logger.error(f"[agent] Failed to create branch {branch_name}: {e}")
        return None


def prepare(task_id, task_title, prompt, repo_path, branch_prefix="maiko",
            auto_kickoff=False, use_worktree=True):
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
    slug = _slugify(task_id)
    branch_name = f"{branch_prefix}-{slug}"

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

    agent_id = f"agent-{branch_name}"

    # Compile learning brief for this agent
    try:
        from planet_maiko.brain.learning.processor import compile_brief
        from planet_maiko.agents.profiles import create_profile

        profile = create_profile(agent_id)
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

    mode = "worktree" if use_worktree else "branch"
    notify = Pupdate(
        id=f"agent-ready-{agent_id}",
        source="maiko",
        source_id=f"agent/{agent_id}",
        type="agent_ready",
        priority="normal",
        title=f"Agent ready: {task_title}",
        body=f"Prepared on branch `{branch_name}` ({mode}). Launch your agent in:\n\n{working_path}",
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
            "claude_code": f"cd {working_path} && claude --dangerously-load-development-channels server:maiko-channel",
            "claude_code_simple": f"cd {working_path} && claude",
            "aider": f"cd {working_path} && aider",
            "manual": f"cd {working_path} && cat TASK.md",
        },
    }

    if auto_kickoff:
        kickoff_result = _kickoff_agent(agent_id, working_path, task_id)
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
            "worktree_path": p.extra.get("worktree_path"),
            "prepared_at": p.timestamp.isoformat() if p.timestamp else None,
        }
        for p in agents
    ]
