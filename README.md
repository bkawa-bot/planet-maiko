# Planet Maiko

**A personal engineering companion that unifies your notifications, triages with AI, and orchestrates coding agents.**

Planet Maiko monitors your GitHub PRs, Linear issues, Calendar, and Slack — then uses a rules engine and LLM-powered brain to auto-triage, create tasks, and surface what matters. When work needs doing, it prepares coding agents in git worktrees and communicates with them through a bidirectional inbox.

## Features

- **Unified Inbox** — All notifications from GitHub, Linear, Calendar, Slack in one stream with smart filtering
- **Smart Brain** — Rules engine handles 80% of triage for free; LLM handles the rest
- **Self-Specializing Agents** — Agents are context sets that evolve through training and real task outcomes
- **Training System** — Train agents on historical PRs with LLM-as-judge scoring
- **Knowledge Pool** — Learns from PR comments and feedback, graduates rules at confidence thresholds
- **Plugin System** — Drop a .py file in `~/.maiko/plugins/` or install via pip entry_points
- **Focus Mode** — Deep focus / soft focus / away modes gate what surfaces by priority
- **Pack Insights** — Collect agent learnings, deduplicate, and merge to global knowledge
- **Task Scheduler** — Smart ordering that groups by repo to minimize context switching
- **Incident Correlation** — Groups related events (CI fail + deploy block) into incidents
- **Scene Engine** — Dynamic pixel-art scenes with live weather overlays (clouds, rain, snow, fog)
- **Configurable Skills** — Morning briefs, brainstorms, PR reviews, investigations — schedulable and extensible
- **Pluggable Runtime** — Default is Claude Code, but bring your own brain

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- `gh` CLI (optional — for GitHub integration)
- Claude Code (optional — for LLM features)

### Install

```bash
# Clone
git clone https://github.com/bkawa-bot/planet-maiko.git
cd planet-maiko

# Backend (use a virtual environment)
python3 -m venv .venv
source .venv/bin/activate    # On Windows: .venv\Scripts\activate
pip install -e .

# Frontend
cd frontend
npm install
cd ..
```

### Run

Open two terminals:

```bash
# Terminal 1 — backend (port 8420)
source .venv/bin/activate    # On Windows: .venv\Scripts\activate
maiko serve

# Terminal 2 — frontend (port 5173)
cd frontend
npm run dev
```

Open **http://localhost:5173** in your browser.

### Configure

Open the app and go to **Settings** (gear icon in the topbar):

1. **Weather** — Type your city or zipcode in the Scene & Weather section, click Lookup. Enables live weather on the homepage via [Open-Meteo](https://open-meteo.com/) (free, no API key needed).
2. **GitHub** — Expand Integrations, enable GitHub, enter your username and repos. Requires `gh auth login` first.
3. **Linear** — Enable, paste your API key and team ID from Linear settings.
4. **Calendar** — Paste your iCal/ICS URL (Google Calendar, Outlook, CalDAV).
5. **Allowed Tools** — Under Agent Preferences, add tools like `Bash, Read, Edit, Write, WebFetch` so agents don't hit permission prompts.

## Architecture

Planet Maiko's brain is modeled on a CPU — each cycle processes instructions through a pipeline:

```
Brain Cycle (every 5 min)
  1. Agent Monitor     → Process agent updates, auto-complete tasks
  2. Conflict Detector → A2A file/API overlap warnings
  3. Correlator        → Group related events into incidents
  4. Pupdate Processor → Match rules (free) → LLM triage (pennies)
  5. Learning          → Aggregate signals into graduated rules
  6. Tournaments       → Auto-train agents on merged PRs
  7. Heartbeats        → Nudge silent agents
  8. Project Driver    → Auto-advance project phases
```

### Self-Specializing Agents

Agents are context sets — each agent has a proven set of learnings that get injected into their coding guidelines:

1. **Pup phase** — New agents explore with random learning combos during training
2. **Training** — LLM-as-judge scores combos against actual merged PR code
3. **Specialization** — Winning combos become the agent's fixed context set
4. **Self-improvement** — Subsequent training tests small variations (add a learning, drop one)

Train agents manually on the Training page, or let the brain cycle do it automatically on merged PRs.

### Agent Communication

Agents get **prepared** in git worktrees with `TASK.md` and `CLAUDE.md`, then you launch them:

```bash
# Planet Maiko prepares the worktree
POST /api/agents/prepare

# You launch the agent
cd .maiko-worktrees/maiko-fix-auth && claude

# Agent checks inbox and reports back
maiko inbox
maiko report "Fixed the retry logic"
maiko task done
```

### Dashboard Pages

| Page | What it does |
|------|-------------|
| **Home** | Focus tasks, recent notifications, calendar, live weather with overlays |
| **Inbox** | Unified notification stream with tabs: All, PRs, Calendar, From Maiko, System |
| **Tasks** | Projects + tasks, AI task generation, pin to focus, agent assignment |
| **Agents** | Active agents, profiles with strengths, message threads, Pack Insights, leaderboard |
| **Knowledge** | Knowledge pool — learnings with approve/dismiss, backfill from PRs |
| **Skills** | Create, edit, run, and schedule skills (morning brief, PR review, brainstorm, etc.) |
| **Training** | Train agents on merged PRs, view training history |

## Plugin System

Extend Planet Maiko without forking the core:

### Local plugins (simplest)

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

### Available hooks

| Hook | When it fires |
|------|--------------|
| `on_startup(app)` | App creation — register blueprints, models |
| `on_brain_cycle(phase, results, app)` | After each brain cycle phase |
| `on_pupdate_created(pupdate)` | New notification created |
| `on_task_created(task)` | New task created |
| `register_commands(subparsers)` | CLI startup — add subcommands |

## Extending Planet Maiko

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

### Add a skill

Create or edit skills directly in the Skills page, or add defaults in `agents/skills/defaults.py`. Skills support scheduling (run every N minutes) and can create notifications from their output.

### Swap the runtime

Implement `AgentRuntime` for any agent engine:

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

## License

[GNU AGPL v3](LICENSE) — Planet Maiko is free software. Anyone who modifies and runs it must share their source code. Private/internal use and plugins are unrestricted.

Created by [Brigitte Kawaguchi](https://github.com/bkawa-bot) · Built with Claude
