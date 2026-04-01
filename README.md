# Planet Maiko

**A personal engineering assistant that unifies your notifications, triages with AI, and orchestrates coding agents.**

Planet Maiko monitors your GitHub PRs, Linear issues, Calendar, and Slack - then uses a rules engine and LLM-powered brain to auto-triage, create tasks, and surface what matters. When work needs doing, it prepares coding agents in git worktrees and communicates with them through a bidirectional inbox.

## Features

- **Unified Inbox** - All notifications from GitHub, Linear, Calendar, Slack in one stream
- **Smart Brain** - Rules engine handles 80% of triage for free; LLM handles the rest
- **Focus Mode** - Deep focus / soft focus / away modes gate what surfaces by priority
- **Coding Agents** - Orchestrate AI agents in git worktrees with bidirectional communication
- **Self-Learning** - Learns from PR comments and feedback, graduates rules at confidence thresholds
- **EOD Gathering** - End-of-day ritual collects agent learnings, deduplicates, merges to global knowledge
- **Task Scheduler** - Smart ordering that groups by repo to minimize context switching
- **Incident Correlation** - Groups related events (CI fail + deploy block) into incidents
- **Expertise Graph** - Who-knows-what map from PR history with time-decayed scoring
- **Suggestions** - Proactively finds stuck PRs, stale tasks, and improvement opportunities
- **Scene Engine** - Dynamic pixel-art scenes based on weather, time, season, and holidays
- **Pluggable Runtime** - Default is Claude Code, but bring your own brain

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- `gh` CLI (for GitHub integration)
- Claude Code (optional, for LLM features)

### Setup

```bash
# Clone
git clone https://github.com/your-username/planet-maiko.git
cd planet-maiko

# Backend
cd backend
pip install -r requirements.txt
cp config.yaml.example config.yaml
# Edit config.yaml with your integrations
python3 app.py

# Frontend (new terminal)
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** in your browser.

### Configure

Edit `backend/config.yaml` to enable integrations:

```yaml
github:
  enabled: true
  username: your-github-username
  repos: ["org/repo1", "org/repo2"]
```

Or use the Settings page in the dashboard.

## Architecture

Planet Maiko's brain is modeled on a CPU - each cycle processes instructions through a pipeline:

```
Brain Cycle (every 5 min)
  1. Agent Monitor    → Process agent pupdates, auto-complete tasks
  2. Correlator       → Group related pupdates into incidents
  3. Pupdate Processor → Match rules (free) → LLM triage (pennies)
  4. Learning         → Aggregate signals into graduated rules
```

### Agent Communication

Agents don't get spawned - they get **prepared**. Planet Maiko creates a git worktree with `TASK.md` and `CLAUDE.md`, then you launch whatever agent you want:

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
├── backend/
│   ├── app.py                  # Flask app factory
│   ├── config.py               # YAML config system
│   ├── models/                 # SQLAlchemy models (6 tables)
│   ├── api/                    # REST API (10 blueprints, ~50 endpoints)
│   ├── brain/
│   │   ├── cycle.py            # CPU-style clock tick
│   │   ├── pupdates/           # Processor, rules, correlator
│   │   ├── tasks/              # Scheduler
│   │   ├── learning/           # Signals → learnings → rules
│   │   ├── focus/              # Focus mode manager
│   │   ├── awareness/          # A2A conflict detection, expertise
│   │   ├── suggestions/        # Improvement scanner
│   │   ├── creativity/         # Scene engine
│   │   └── guardrails.py       # Action permission levels
│   ├── pollers/                # GitHub, Linear, Calendar
│   └── agents/                 # Orchestrator, monitor, brain session
├── frontend/                   # React + Vite
│   └── src/
│       ├── pages/              # 12 pages
│       └── components/         # Header, Layout
└── cli/
    └── maiko                   # CLI for agent communication
```

## Pluggable Brain

The brain runtime is swappable. Default is Claude Code, but you can implement the `AgentRuntime` interface for any agent:

```python
# backend/agents/runtimes/base.py
class AgentRuntime(ABC):
    def send(self, prompt, working_dir=None, timeout=300): ...
    def is_available(self): ...
```

```yaml
# config.yaml
brain:
  runtime: claude-code  # or your custom runtime
```

## Self-Learning System

Planet Maiko learns from your team's PR comments:

1. **Signals** - Raw feedback events ("alice flagged null safety on repo-x")
2. **Aggregation** - Similar signals accumulate with confidence scores
3. **Graduation** - At threshold (2-5 signals depending on category), rules become active
4. **Brief** - Active rules compiled into coding guidelines for agents

High-stakes categories (security, API design) require manual approval before graduating.

## Contributing

This is an open source project. Contributions welcome!

- **Add an integration** - Extend `BasePoller` in `pollers/`
- **Add a brain runtime** - Implement `AgentRuntime` in `agents/runtimes/`
- **Add a skill** - Add a prompt template in `agents/skills/prompts.py`
- **Add brain rules** - Add to `DEFAULT_RULES` in `brain/pupdates/rules.py`

## License

[GNU LGPL v2.1](LICENSE) — Planet Maiko is free software. The core must stay open source.
Plugins and extensions can be any license, including proprietary.

Created by [Brigitte Kawaguchi](https://github.com/bkawa-bot) · Built with Claude
