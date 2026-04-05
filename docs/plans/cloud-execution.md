# Cloud Agent Execution

## Overview

Run coding agents in the cloud instead of locally. Phased approach that works today with git-mediated communication, and swaps in native `claude --remote` when Anthropic ships it.

## Reality Check

`claude --remote` does not exist yet in CLI v2.1.92. The plan is designed so that Phases 0-3 work today, and Phase 4 is a clean swap when the API arrives.

## Phases

### Phase 0 -- Refactor (now)
- `CloudSession` tracking model in the database
- Refactor `prepare()` to separate planning logic from execution
- Add `execution_mode` toggle (local / cloud)

### Phase 1 -- GitHub-Mediated Communication (now)
- Agent pushes to its branch; Maiko detects updates via a poller
- PR creation signals task completion
- No real-time connection needed

### Phase 2 -- Optional Real-Time Comms (now)
- Tunnel support (ngrok / cloudflare) for live status updates
- File-based relay fallback: `.maiko/inbox.json` committed to the branch
- Graceful degradation if tunnel is unavailable

### Phase 3 -- Async Tournaments
- Dispatch tournament entries as independent cloud sessions
- Collect results asynchronously; don't block the brain cycle
- Enables parallel evaluation of multiple agent submissions

### Phase 4 -- Native Remote (when Anthropic ships)
- Swap in `claude --remote` command
- Use session status API for monitoring
- Cloud MCP support for real-time tool access
- `--append-system-prompt` in remote mode

## Blockers from Anthropic

- `--remote` command
- Session status API
- Cloud MCP server support
- `--append-system-prompt` in remote mode

## Key Files

- `coding_agent.py` -- add `prepare_cloud()` separation
- `cloud_claude_code.py` -- new cloud execution runtime
- `cloud_session.py` -- new CloudSession model
- `cycle.py` -- cloud results collection phase in brain cycle
