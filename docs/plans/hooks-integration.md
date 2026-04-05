# Claude Code Hooks for Agent Automation

## Overview

Integrate Claude Code hooks (pure Python, no dependencies) so that agent worktrees automatically report status, re-inject context after compaction, and create pupdates on milestones.

## Hook Scripts (hooks/ directory)

| Script | Trigger | Purpose |
|--------|---------|---------|
| `post_tool_use.py` | PostToolUse | Auto-report to Maiko on git commit/push |
| `post_compact.py` | PostCompact | Re-inject learnings via agent inbox after context compression |
| `notification.py` | Notification | Create pupdates when agents hit milestones |
| `subagent_stop.py` | SubagentStop | Report when subagents finish their work |

All scripts are pure Python with no external dependencies.

## Worktree Setup

Each agent worktree receives two files at creation time:

- `.claude/settings.json` -- hook configuration pointing to the hook scripts
- `.maiko-env.json` -- agent identity (agent ID, task ID, Maiko server URL)

## API Endpoints

Four new endpoints under `/api/hooks/` receive hook payloads:

- `POST /api/hooks/report` -- status update from PostToolUse
- `POST /api/hooks/reinject` -- context re-injection from PostCompact
- `POST /api/hooks/pupdate` -- milestone notification
- `POST /api/hooks/subagent` -- subagent completion

## Configuration

Config toggle in `config.py` to enable/disable individual hooks. Each hook type can be independently turned on or off.

## Key Files

- `coding_agent.py` -- add `_write_claude_settings()` to set up hooks in worktrees
- `agents_api.py` -- hook receiver endpoints
- `hooks/` -- new directory with the four hook scripts
- `config.py` -- hook enable/disable toggles
