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


def _write_task_file(worktree_path, task_id, task_title, prompt):
    """Write TASK.md in the worktree so the agent knows what to do."""
    content = f"""# Task: {task_title}

**Task ID:** {task_id}
**Created:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

## Instructions

{prompt}

## Communication with Planet Maiko

### Check for messages (after each commit or before starting a new subtask)
```bash
maiko inbox
```

### Report progress
```bash
maiko report "Your status update"
```

### Reply to Maiko
```bash
maiko reply "Your response"
```

### When done
```bash
maiko task done
```

### If stuck
```bash
maiko task stuck -m "Describe what's blocking you"
```

## Rules

- Work in this directory (a git worktree on its own branch)
- Make small, focused commits as you go
- **Check `maiko inbox` after each commit or before starting a new subtask** - Maiko may have new context or direction
- If you get stuck, report it so the brain can help or reassign
- When done, make sure tests pass, then report completion
"""
    with open(os.path.join(worktree_path, "TASK.md"), "w") as f:
        f.write(content)


def _write_claude_md(worktree_path, task_id, task_title, maiko_port=8420):
    """Write CLAUDE.md so Claude Code picks up context automatically."""
    content = f"""# Agent Context

You are a coding agent working on a task for Planet Maiko.

## Your Task
Read TASK.md in this directory for full instructions.

**Task:** {task_title}
**Task ID:** {task_id}

## Communication

Planet Maiko runs at http://localhost:{maiko_port}. Use the `maiko` CLI to communicate:

**Check your inbox after each commit or before starting a new subtask:**
```bash
maiko inbox
```

Report progress and completion:
```bash
maiko report "What you did"
maiko task done
```

If you need to reply to a message from Maiko:
```bash
maiko reply "Your response"
```

## Rules
- Stay focused on the task in TASK.md
- Commit your work as you go
- **Check `maiko inbox` after commits or between subtasks** - Maiko may send updated context or new direction
- Report back when done or if you're stuck
"""
    claude_dir = os.path.join(worktree_path, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    # Write as CLAUDE.md in the project root for Claude Code to pick up
    with open(os.path.join(worktree_path, "CLAUDE.md"), "w") as f:
        f.write(content)


def prepare(task_id, task_title, prompt, repo_path, branch_prefix="maiko"):
    """Prepare a worktree for an agent to work in.

    This does NOT launch the agent. It sets up everything the agent
    needs and returns instructions for the user to launch it.

    Args:
        task_id: the task this agent will work on
        task_title: human-readable task title
        prompt: full instructions for the agent
        repo_path: path to the git repository
        branch_prefix: prefix for the branch name

    Returns:
        dict with agent info and launch instructions, or None on failure
    """
    slug = _slugify(task_id)
    branch_name = f"{branch_prefix}-{slug}"

    worktree_path = _create_worktree(repo_path, branch_name)
    if not worktree_path:
        return None

    # Write task files
    _write_task_file(worktree_path, task_id, task_title, prompt)
    _write_claude_md(worktree_path, task_id, task_title)

    agent_id = f"agent-{branch_name}"

    # Create a pupdate to notify the user
    notify = Pupdate(
        id=f"agent-ready-{agent_id}",
        source="maiko",
        source_id=f"agent/{agent_id}",
        type="agent_ready",
        priority="normal",
        title=f"Agent ready: {task_title}",
        body=f"Worktree prepared on branch `{branch_name}`. Launch your agent in:\n\n{worktree_path}",
        actionable=True,
        action_hint="Launch agent",
        tags=[task_id, "agent"],
        extra={
            "agent_id": agent_id,
            "branch": branch_name,
            "worktree_path": worktree_path,
            "task_id": task_id,
        },
    )
    db.session.add(notify)
    db.session.commit()

    logger.info(f"[orchestrator] Prepared agent {agent_id} at {worktree_path}")

    return {
        "agent_id": agent_id,
        "task_id": task_id,
        "branch": branch_name,
        "worktree_path": worktree_path,
        "status": "ready",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "launch_instructions": {
            "claude_code": f"cd {worktree_path} && claude",
            "aider": f"cd {worktree_path} && aider",
            "manual": f"cd {worktree_path} && cat TASK.md",
        },
    }


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
