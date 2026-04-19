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

- **Pupdates** — *things to notice.* Notifications from your pollers (GitHub, Linear, Calendar) plus internal events. Surface on **Home**. The brain cycle triages them into the other concepts below.
- **Tasks** — *things to finish.* Typed into Maiko, created automatically from actionable pupdates, or auto-spawned (e.g. an incident investigation). Live on **Tasks**. An agent can be assigned.
- **Agents** — *your pack.* Personas with role (coding / review / investigation / cartographer), scope (a repo or "global"), and a LoRA adapter that shapes their competence. Each agent runs in its own git worktree. Live on **Agents**.
- **Insights** — *tribal knowledge your agents inherit.* Short notes like *"use IntelliJ for tests, the CLI runner is broken"*. Written by agents during work or typed by you. Approved insights inject verbatim into every new agent's `CLAUDE.md`.
- **Learnings** — *coding rules the LoRA trainer trains on.* Extracted from PR review comments, agent feedback, and Pack Insights rituals. Live on **Knowledge**. Aggregate into per-repo LoRA adapters via **Training**.
- **Automations** — *prompt templates Maiko can run on a schedule.* Morning briefs, custom user-authored scripts. Live on **Automations**.

The **Pack Insights** ritual is where the pack gathers around the campfire at end of day — active agents share feedback (→ Learnings → LoRA) and insights (→ CLAUDE.md). You approve per-agent what sticks. The pack learns together; nothing sneaks into the model weights without your nod.

Glance surfaces on the topbar:

- **Health dot** — green / yellow / red for pollers, brain cycle, last backup. Hover for details.
- **Focus mode** — available / soft focus / deep focus / away. Gates what notifications reach you.
- **Weekend mode** — toggle off-duty; ambient work pauses, nothing nudges you.
- **Power button** — end-of-day shutdown ritual. Prunes old data, tucks agents in, stops the server.

## Architecture

Maiko's brain is modeled on a CPU — each cycle processes instructions through a pipeline:

```
Brain Cycle (every 5 min)
  1. Agent Monitor     → Process agent updates, auto-complete tasks
  2. Conflict Detector → A2A file/API overlap warnings across the pack
  3. Correlator        → Group related events into incidents
  4. Pupdate Processor → Match rules (free) → LLM triage (pennies)
  5. Learning          → Aggregate signals into graduated rules
  6. Heartbeats        → Auto-wake silent agents; flag stuck ones
  7. Project Driver    → Auto-advance project phases
```

### How agents work

When you assign an agent to a task, Maiko prepares a git worktree with `TASK.md`, `CLAUDE.md` (protocol + injected Learnings), and `.mcp.json` (wiring the maiko-channel MCP plus any per-repo MCPs you already use). Then a headless `claude --print` run kicks off in the worktree — no terminals, no `--dangerously-*` flags to remember.

The agent works, commits to its branch, and calls `reply(message_type="ready_for_review")` via the channel MCP when it's done. You see the diff in-app, leave inline comments, and either approve (Maiko pushes + opens the PR) or request changes (the agent auto-wakes, reads your comments, iterates). The wake orchestrator guarantees two triggers can't race — every resume goes through a single lock.

### Dashboard pages

| Page | What it does |
|------|-------------|
| **Home** | Pack Requests, rolling overview pane, scene + weather, pet Maiko |
| **Tasks** | Projects + tasks, AI task generation, agent assignment |
| **Agents** | Active pack (live state dots), profiles, message threads, Pack Insights |
| **Knowledge** | Learnings with approve/dismiss, Insights playbook, backfill from PRs |
| **Automations** | Create, edit, run, schedule custom skills |
| **Training** | Train per-repo LoRA adapters on merged PRs, view training history |

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

Subclass `BasePoller` and register as an entry point:

```python
from planet_maiko.pollers.base import BasePoller

class PagerDutyPoller(BasePoller):
    name = "pagerduty"
    def poll(self, config): ...
    def to_pupdates(self, raw_data): ...
```

```toml
[project.entry-points."planet_maiko.pollers"]
pagerduty = "my_package:PagerDutyPoller"
```

### Swap the runtime

Implement `AgentRuntime` for any agent engine (not just Claude Code):

```python
class AgentRuntime(ABC):
    def send(self, prompt, working_dir=None, timeout=300): ...
    def is_available(self): ...
```

```toml
[project.entry-points."planet_maiko.runtimes"]
my-runtime = "my_package.runtime:MyRuntime"
```

## CLI Reference

```
maiko serve [--host] [--port] [--debug]   Start the server
maiko report "message"                     Send update from agent
maiko task done|start|stuck [task-id]      Update task status
maiko inbox [--all]                        Check messages from brain
maiko reply "message"                      Reply to brain
maiko feedback "msg" --category testing    Send in-session feedback
maiko status                               Check brain/runtime status
maiko bootstrap [--limit 20]               Seed learnings from past PRs
```
