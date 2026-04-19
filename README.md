# Planet Maiko

**Your final teammate.**

A personal engineering companion that unifies your notifications, triages with AI, and orchestrates coding agents that learn from your team.

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

# Agent channel (for real-time agent communication)
cd channel
npm install
cd ..
```

> **Mac users:** If you see SSL errors with Linear or other integrations, run:
> `pip install --upgrade certifi` and ensure your Python has certificates installed
> (run `open /Applications/Python\ 3.12/Install\ Certificates.command` if needed).

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

## Mental model

Planet Maiko shuffles data between a few core concepts. Knowing which
is which makes every page legible:

- **Pupdates** — *things to notice.* Notifications from your pollers
  (GitHub, Linear, Slack, Calendar) plus internal events. Land in the
  **Inbox**. Some are "action" (blocks on you), most are "activity"
  (ambient). Brain cycle triages them into the other concepts below.
- **Tasks** — *things to finish.* Actual work items. Can be typed
  into Maiko by hand, created automatically from an actionable
  pupdate, or auto-spawned (e.g. an incident investigation). Live on
  the **Tasks** page. An agent can be assigned to a task.
- **Agents** — *your pack.* Personas with role (coding / review /
  investigation / cartographer), scope (a repo or "global"), and a
  context set that shapes their behavior. Live on the **Agents**
  page. Each agent runs in a git worktree with its own CLAUDE.md.
- **Insights** — *tribal knowledge your agents inherit.* Short notes
  like *"Use IntelliJ for tests, the CLI runner is broken"*. Written
  by agents during work, or typed by you. Live in the **Pack Insights
  library** on the Agents page. Approved insights are injected
  verbatim into every new agent's CLAUDE.md.
- **Learnings** — *coding rules the LoRA trainer trains on.*
  Extracted from PR review comments, agent feedback, and the Pack
  Insights ritual. Live on the **Knowledge** page. Aggregate into a
  per-repo LoRA adapter via the **Training** page.
- **Automations** — *prompt templates Maiko can run on a schedule.*
  Morning briefs, custom user-authored scripts. Live on the
  **Automations** page. Scheduled briefings live under **Settings →
  Scheduled Briefings** instead.

The **Pack Insights** tab on Agents is where the pack gathers around
the campfire at end of day — every active agent is asked to share
feedback (→ Learnings → LoRA) and insights (→ CLAUDE.md). You approve
per-agent what sticks.

Status you can glance at:

- **Home** — scene, morning brief, today's activity digest, what's
  waiting on you.
- **Topbar health dot** — green / yellow / red status for pollers,
  brain cycle, and last backup. Hover for details.
- **Topbar power button** — end-of-day shutdown ritual: prunes old
  data, tucks agents in, stops the server.

## Architecture

Planet Maiko's brain is modeled on a CPU — each cycle processes instructions through a pipeline:

```
Brain Cycle (every 5 min)
  1. Agent Monitor     → Process agent updates, auto-complete tasks
  2. Conflict Detector → A2A file/API overlap warnings
  3. Correlator        → Group related events into incidents
  4. Pupdate Processor → Match rules (free) → LLM triage (pennies)
  5. Learning          → Aggregate signals into graduated rules
  6. Heartbeats        → Nudge silent agents
  7. Project Driver    → Auto-advance project phases
```

### Self-Specializing Agents

Agents are context sets — each agent has a proven set of learnings that get injected into their coding guidelines:

1. **Pup phase** — New agents explore with random learning combos during training
2. **Training** — LLM-as-judge scores combos against actual merged PR code
3. **Specialization** — Winning combos become the agent's fixed context set
4. **Self-improvement** — Subsequent training tests small variations (add a learning, drop one)

Train agents manually on the Training page, or let the brain cycle do it automatically on merged PRs.

### Agent Communication

Agents get **prepared** in git worktrees with `TASK.md`, `CLAUDE.md`, and `.mcp.json`, then you launch them:

```bash
# Planet Maiko prepares the worktree
POST /api/agents/prepare

# Launch with real-time Maiko channel (recommended)
cd .maiko-worktrees/maiko-fix-auth
claude --dangerously-load-development-channels server:maiko-channel

# Or launch without the channel (uses polling fallback)
cd .maiko-worktrees/maiko-fix-auth && claude
```

**With the channel:** Maiko pushes messages directly into the Claude session — nudges, questions, sleep signals arrive instantly. The agent replies via the `reply` tool.

**Without the channel:** The agent polls manually using the CLI:
```bash
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

Planet Maiko is [AGPL v3](LICENSE). In plain English:

- **Use it anywhere, solo or inside a company.** No strings.
- **Modify it for your team's own use.** AGPL asks that you share your source with anyone who uses your instance — when "anyone" means your coworkers, pointing them at your internal branch is enough. You don't have to publish anything to the world.
- **Build a paid product on top of it?** You must share your modifications under AGPL too. That's the anti-extraction intent — if someone commercializes Maiko, the community gets the improvements back.

Not legal advice, just the intent. If you're using Maiko to help yourself or your team, you're free. If you're selling it, share back.

Created by [Brigitte Kawaguchi](https://github.com/bkawa-bot) · Built with Claude
