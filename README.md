# Planet Maiko

**A personal engineering companion that unifies your notifications, triages with AI, and orchestrates coding agents.**

Planet Maiko monitors your GitHub PRs, Linear issues, Calendar, and Slack — then uses a rules engine and LLM-powered brain to auto-triage, create tasks, and surface what matters. When work needs doing, it prepares coding agents in git worktrees and communicates with them through a bidirectional inbox.

## Features

- **Unified Inbox** — All notifications from GitHub, Linear, Calendar, Slack in one stream with smart filtering
- **Smart Brain** — Rules engine handles 80% of triage for free; LLM handles the rest
- **Focus Mode** — Deep focus / soft focus / away modes gate what surfaces by priority
- **Coding Agents** — Orchestrate AI agents in git worktrees with bidirectional communication
- **Self-Learning** — Learns from PR comments and feedback, graduates rules at confidence thresholds
- **Pack Insights** — Collect agent learnings, deduplicate, and merge to global knowledge
- **Task Scheduler** — Smart ordering that groups by repo to minimize context switching
- **Incident Correlation** — Groups related events (CI fail + deploy block) into incidents
- **Suggestions** — Proactively finds stuck PRs, stale tasks, and improvement opportunities
- **Scene Engine** — Dynamic pixel-art scenes based on live weather, time, season, and holidays
- **Configurable Skills** — Morning briefs, brainstorms, investigations — schedulable and extensible
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

# Backend
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

## Architecture

Planet Maiko's brain is modeled on a CPU — each cycle processes instructions through a pipeline:

```
Brain Cycle (every 5 min)
  1. Agent Monitor     → Process agent updates, auto-complete tasks
  2. Conflict Detector → A2A file/API overlap warnings
  3. Correlator        → Group related events into incidents
  4. Pupdate Processor → Match rules (free) → LLM triage (pennies)
  5. Learning          → Aggregate signals into graduated rules
```

### Agent Communication

Agents don't get spawned — they get **prepared**. Planet Maiko creates a git worktree with `TASK.md` and `CLAUDE.md`, then you launch whatever agent you want:

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

### Project Structure

```
planet-maiko/
├── src/planet_maiko/
│   ├── app.py                  # Flask app factory
│   ├── config.py               # YAML config system (XDG-compliant)
│   ├── database.py             # SQLAlchemy + SQLite
│   ├── models/                 # 8 core models
│   ├── api/                    # 11 API blueprints, ~50 endpoints
│   ├── brain/
│   │   ├── cycle.py            # CPU-style clock tick
│   │   ├── pupdates/           # Processor, rules, correlator
│   │   ├── tasks/              # Scheduler
│   │   ├── learning/           # Signals → learnings → rules + tournaments
│   │   ├── focus/              # Focus mode manager
│   │   ├── awareness/          # A2A conflict detection, expertise graph
│   │   ├── suggestions/        # Improvement scanner
│   │   ├── creativity/         # Scene engine (weather, seasons, holidays)
│   │   └── guardrails.py       # Autonomous / semi / needs-confirmation tiers
│   ├── pollers/                # GitHub, Linear, Calendar (pluggable)
│   ├── agents/                 # Orchestrator, monitor, profiles, brain session
│   │   ├── runtimes/           # Claude Code (default), extensible
│   │   └── skills/             # Prompt templates (morning-brief, brainstorm, etc.)
│   └── cli/main.py             # `maiko` CLI
├── frontend/                   # React 19 + Vite
│   └── src/
│       ├── pages/              # Home, Inbox, Tasks, Agents, Brain, Settings
│       └── components/         # Topbar, Layout, Toast
└── pyproject.toml              # pip-installable package
```

### Dashboard Pages

| Page | What it does |
|------|-------------|
| **Home** | Focus tasks, recent notifications, calendar, live weather scene |
| **Inbox** | Unified notification stream with tabs: All, PRs, Calendar, From Maiko, System |
| **Tasks** | Projects + tasks in a unified view, AI task generation, agent assignment |
| **Agents** | Active agents, profiles, message threads, Pack Insights (knowledge sharing) |
| **Brain** | Knowledge pool (learnings) + Skills (create, edit, run, schedule) |

## Pluggable Brain

The brain runtime is swappable. Default is Claude Code, but you can implement the `AgentRuntime` interface for any agent:

```python
# src/planet_maiko/agents/runtimes/base.py
class AgentRuntime(ABC):
    def send(self, prompt, working_dir=None, timeout=300): ...
    def is_available(self): ...
```

Register your runtime as a Python entry point:

```toml
# pyproject.toml
[project.entry-points."planet_maiko.runtimes"]
my-runtime = "my_package.runtime:MyRuntime"
```

## Self-Learning System

Planet Maiko learns from your team's PR comments:

1. **Signals** — Raw feedback events ("alice flagged null safety on repo-x")
2. **Aggregation** — Similar signals accumulate with confidence scores
3. **Graduation** — At threshold (2-5 signals depending on category), rules become active
4. **Tournament** — Merged PRs as ground truth; 4 strategies compete; LLM-as-judge scoring
5. **Brief** — Active rules compiled into coding guidelines, ranked by real task outcomes

High-stakes categories (security, API design) require manual approval before graduating.

## Extending Planet Maiko

### Add an integration

Subclass `BasePoller` and register as an entry point:

```python
# my_poller.py
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

Create or edit skills directly in the Brain > Skills tab, or add defaults in `agents/skills/prompts.py`. Skills support scheduling (run every N minutes) and can create notifications from their output.

### Add brain rules

Add to `DEFAULT_RULES` in `brain/pupdates/rules.py`, or contribute rules via the plugin entry point system.

## CLI Reference

```
maiko serve [--host] [--port] [--debug]   Start the server
maiko report "message"                     Send update from agent
maiko task done|start|stuck [task-id]      Update task status
maiko inbox [--all]                        Check messages from brain
maiko reply "message"                      Reply to brain
maiko status                               Check brain/runtime status
```

## License

[GNU LGPL v2.1](LICENSE) — Planet Maiko is free software. The core must stay open source.
Plugins and extensions can be any license, including proprietary.

Created by [Brigitte Kawaguchi](https://github.com/bkawa-bot) · Built with Claude
