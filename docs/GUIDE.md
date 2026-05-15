# Planet Maiko — Developer Guide

The conceptual model, the brain's pipeline, the plugin system, and how to extend it. For the pitch and quick-start, see the main [README](../README.md).

## Configure

After walking through the setup wizard, open **Settings** (gear icon in the topbar) and wire up the integrations you use:

1. **Weather** — type your city, click Lookup. Live weather via [Open-Meteo](https://open-meteo.com/) (free, no key).
2. **GitHub** — enable, enter username + repos. Requires `gh auth login` first.
3. **Linear** — paste API key + team ID from Linear settings.
4. **Calendar** — paste iCal/ICS URL (Google Calendar, Outlook, CalDAV).
5. **Allowed Tools** — add agent tools like `Bash, Read, Edit, Write, WebFetch` so agents don't hit permission prompts mid-session.

## Mental model

Maiko shuffles data between a few core concepts. Knowing which is which makes every page legible:

- **Pupdates** — *things to notice.* Notifications from your pollers (GitHub, Linear, Calendar, PagerDuty) plus internal events. Surface on **Home** as memos. The brain cycle triages them into the other concepts below.
- **Tasks** — *things to finish.* Typed into Maiko, created automatically from actionable pupdates, or spawned by Automation rules. Live on **Tasks**. An agent can be assigned.
- **Agents** — *your pack.* Characters with a name, an avatar, a role (coding / review / investigation / cartographer), and a scope (a repo or "global"). Run in isolated git worktrees with their own `CLAUDE.md`. Live on **Pack**.
- **Insights** — *tribal knowledge your agents inherit.* Short notes like *"use IntelliJ for tests, the CLI runner is broken"*. Written by agents during work or typed by you. Approved insights inject verbatim into every new agent's `CLAUDE.md`.
- **Learnings** — *coding rules for review agents.* Extracted from PR review comments, agent feedback, and Pack Insights rituals. Live on **Knowledge**. Review agents retrieve the ones relevant to the diff they're reading via `maiko rules-relevant` — that's how your team's accumulated taste shows up in agent reviews.
- **Automations** — *when→then rules.* "When a PR I'm tagged on goes stale, leave me a memo." "When a coding agent finishes, ping me in chat." Built-in defaults ship pre-wired; user-authored ones (and **Specialties** — pre-built role playbooks an agent adopts for a particular kind of work) live on **Automations**.

The **Pack Insights** ritual is where the pack gathers around the campfire at end of day — active agents share feedback (→ Learnings) and insights (→ playbook). You approve per-agent what sticks. The pack learns together; nothing slips into agent context without your nod.

Glance surfaces on the topbar:

- **Health dot** — green / yellow / red for pollers, brain cycle, last backup. Hover for details.
- **Weekend mode** — toggle off-duty; ambient work pauses, nothing nudges you.
- **Power button** — end-of-day shutdown ritual. Prunes old data, tucks agents in, stops the server.
- **Pack dock (left edge)** — every agent currently running. Hover for their last status.

## Architecture

Maiko's brain is modeled on a CPU — each cycle processes instructions through a pipeline:

```
Brain Cycle (every 5 min)
  1. Agent Monitor     → Process agent updates, auto-complete tasks
  2. Conflict Detector → A2A file/API overlap warnings across the pack
  3. Pupdate Processor → Match rules (free) → LLM triage (pennies)
  4. Learning          → Aggregate signals into graduated rules
  5. Heartbeats        → Auto-wake silent agents; flag stuck ones
  6. Project Driver    → Auto-advance project phases
```

### How agents work

When you assign an agent to a task, Maiko prepares a git worktree with `TASK.md`, `CLAUDE.md` (role protocol + character + active team Insights + the agent's bio), and an optional `.mcp.json` carrying any project MCPs you have configured in the parent repo. Then the active agent runtime spawns the work — `claude --print` headless by default, or `claude` interactive in a tmux pane if you've flipped `brain.runtime` to `claude-code-tmux` (Mac only, bills against the subscription pool instead of the Agent SDK credit). See [AGENT_RUNTIME.md](AGENT_RUNTIME.md) for the runtime contract and how to add new backends.

The agent works, commits to its branch, and calls `maiko reply "<summary>" --type ready_for_review` from inside the worktree when it's done. The `maiko` CLI (and a small set of Claude Code hooks) is the talk-back path — agents post to Maiko via shell commands rather than MCP tools, which means the same agent loop works under non-Claude runtimes (Ollama for local-model loops, Aider, etc.) without rewiring. You see the diff in-app, leave inline comments, and either approve (Maiko pushes + opens the PR) or request changes (the agent auto-wakes via `claude --resume`, reads your comments, iterates). The wake orchestrator guarantees two triggers can't race — every resume goes through a single lock.

### Dashboard pages

| Page | What it does |
|------|-------------|
| **Home** | Overview narrative, calendar, scene + weather, what's waiting on you |
| **Tasks** | Projects + tasks, AI task generation, agent assignment |
| **Pack** | Active pack (live state dots), profiles, message threads, Pack Insights |
| **Knowledge** | Learnings with approve/dismiss, Insights playbook, backfill from PRs |
| **Automations** | when→then rules + Specialties (per-task role playbooks) |

## Plugin System

Extend Maiko without forking the core.

### Local plugins

Drop a `.py` file in `~/.maiko/plugins/`:

```python
from planet_maiko.plugins.base import MaikoPlugin

class MyPlugin(MaikoPlugin):
    name = "my-plugin"

    def on_startup(self, app):
        print("Plugin loaded!")

    def on_brain_cycle(self, phase, results, app):
        if phase == "learning":
            print(f"Learnings processed: {results}")

    def on_pupdate_created(self, pupdate):
        print(f"New notification: {pupdate.title}")
```

### Pip packages

```toml
# In your plugin's pyproject.toml
[project.entry-points."planet_maiko.plugins"]
my-plugin = "maiko_my_plugin:MyPlugin"
```

Install with `pip install maiko-my-plugin` — auto-discovered on startup.

### Hooks

| Hook | When it fires |
|------|--------------|
| `on_startup(app)` | App creation — register blueprints, models |
| `on_brain_cycle(phase, results, app)` | After each brain cycle phase |
| `on_pupdate_created(pupdate)` | New notification created |
| `on_task_created(task)` | New task created |
| `register_commands(subparsers)` | CLI startup — add subcommands |

## Extending

### Add an integration

Integrations are plugins that fetch on a schedule. Subclass `PollerPlugin`,
implement `poll()` and `to_pupdates()`, and register as a plugin entry point:

```python
from planet_maiko.plugins.helpers import PollerPlugin

class PagerDutyPlugin(PollerPlugin):
    name = "pagerduty"

    def get_config_defaults(self):
        return {"pagerduty": {"enabled": False, "poll_interval_minutes": 5, "api_token": ""}}

    def poll(self, config): ...
    def to_pupdates(self, raw_data): ...
```

```toml
[project.entry-points."planet_maiko.plugins"]
pagerduty = "my_package:PagerDutyPlugin"
```

The plugin fires inside the brain cycle's `on_brain_cycle` hook and gates
on `poll_interval_minutes`. No threads to manage.

### Swap the runtime

Maiko ships three runtimes out of the box (`claude-code` headless, `claude-code-tmux` interactive on Mac, and `ollama` for local-model internal calls). Adding a new one means subclassing `AgentRuntime` in `src/planet_maiko/agents/runtimes/`:

```python
from planet_maiko.agents.runtimes.base import AgentRuntime

class MyRuntime(AgentRuntime):
    name = "my-runtime"

    def is_available(self): ...
    def send(self, prompt, **kwargs): ...
    def send_json(self, prompt, **kwargs): ...

    # Optional — implement these only if the runtime can drive
    # multi-tool agents in a worktree. Sync-only runtimes (e.g. Ollama)
    # skip them and Maiko routes agent kickoffs to a different backend.
    def spawn(self, working_dir, initial_prompt, session_id, **kwargs): ...
    def resume(self, working_dir, session_id, prompt, **kwargs): ...
    def session_transcript_path(self, session_id, working_dir=None): ...
```

Wire it into the dispatch in `agents/brain_session._instantiate_runtime`, then point `brain.runtime` (or `routing.runtime_rules.<task_type>`) at it in config. See [AGENT_RUNTIME.md](AGENT_RUNTIME.md) for the full contract, including how protocol prompts stay runtime-agnostic via the `maiko` CLI.

## CLI Reference

The `maiko` CLI is both the user's admin tool AND the agent-side talk-back path. Anything an agent can do via the CLI also works from your own shell — useful for scripting Maiko interactions from outside an agent session.

**Server / admin:**

```
maiko serve [--host] [--port] [--debug]    Start the server
maiko status                                Check brain/runtime status
maiko backup                                Take a DB snapshot now
maiko bootstrap [--limit 20]                Seed learnings from past PRs
```

**Agent-side (auto-resolves MAIKO_JOB_ID from env or TASK.md):**

```
maiko reply "<text>" --type <type>          Send a message back to Maiko
                                              types: status / message / feedback /
                                                     insight / stuck / ready_for_review
                                                     / plan_for_approval / pr_opened
                                              Add --recipient user to surface in
                                              the user's memos.
maiko inbox [--all]                         Pull messages queued for the agent
maiko check-code [--timeout 120]            Run mechanical checks (tests / lint /
                                              typecheck); exits non-zero if blocked
maiko leave-comment <file> <line> "<body>"  Inline review comment on a diff line
                                              [--side new|old]
maiko session-report [--session-id ID]      Tell Maiko which runtime session this
                                              run is using (defaults to env)
maiko rules-relevant --query "<text>"       Query team rules relevant to a change
                                              (one --query per logical piece)
```
