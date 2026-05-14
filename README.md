# Planet Maiko

**strange agents, strange world.**

We live in strange times, and yet our dev tools are so painfully un-strange. No one knows what being a software engineer will be like in a year, or even a month. If we are all tokenmaxxing to our own inevitable obsolescence, we might as well have fun.

Planet Maiko revolves around one central idea: a tight-knit pack is more powerful (and fun) than a swarm of strangers. Your pack has real agency. They get to name themselves. They know and learn from each other and grow everyday. You will remember them, and they will remember you.

On Planet Maiko, everyone contributes, everyone shares, and no one makes the same mistake twice.

![Planet Maiko home](docs/screenshots/home.png)

> **IMPORTANT 5/14 UPDATE FOR CLAUDE USERS: MAIKO USES _INTERACTIVE_ CLAUDE SESSIONS. THIS MEANS IT WILL PULL FROM YOUR REGULAR SUBSCRIPTION, NOT AGENT SDK CREDITS.**

> **⚠️ Beta.** Planet Maiko is in active beta testing. Expect breaking changes, schema migrations, rough edges, and the occasional bug. If you try it and something's broken, [file an issue](https://github.com/bkawa-bot/planet-maiko/issues/new). Feedback shapes what ships next.

**What (boring) stuff Planet Maiko does:**

All the typical agent orchestration work. Automatically kicks off agents, lets them yell at each other (with Maiko as the mediator), agent task lifecycles, context sharing, etc.

**What (COOL) stuff Planet Maiko does:**

- Automatically curates and updates a knowledge base of you and your team's preferences, coding guidelines, and ongoing context, so your agents stop making the same wrong assumptions every time.
- Builds a history of all your (and your team's) past PR review mistakes for your agents to laugh at (use) so they don't make the same ones.
- Agents aren't off the hook either. Maiko makes them confess their own mistakes too. Everyone learns.
- Has a built-in RAG system of your team's conventions and feedback that populates itself. Agents pull only the rules relevant to what they're working on, so we don't have to stuff 300 rules into every prompt.
- Plug-in architecture for everything else. Wire up your internal tools, your monitoring stack, whatever crazy new shiny thing you're trying this week.
- No venture capitalism, no productivity-maxxing (unless you want). I am not trying to make money. I don't care about maximizing the value you bring to your company. Planet Maiko is for you, not your company.

I use Planet Maiko everyday and it is the only tool I use daily (other than IDEs and GH). If I have to even look at a different dashboard or deal with another agent shouting at me, I get annoyed and just figure out how to make Maiko deal with it instead.

Build a plugin for any tool you never want to have to look at again.

**Current integrations:**
- PagerDuty
- Linear
- Calendar
- GitHub

![The campfire, end of day pack insights](docs/screenshots/campfire.png)

## Full features

### The pack
- Agent orchestration. Maiko kicks off agents, manages their lifecycles, mediates conflicts.
- Worktree-isolated runs. Each agent works in its own git worktree, so siblings don't step on each other.
- A2A conflict detection. Catches file and API overlap between sibling agents before damage is done.
- In-app diff review. Read the agent's PR diff, leave comments, request changes or approve, without leaving Maiko.
- Per-agent personalities pulled from the deck. Agents have names, archetypes, and opinions.

### Memory and learning
- RAG retrieval over your team's accumulated conventions. Agents fetch only what's relevant.
- Learnings extracted from your PR review history. Reviewer feedback becomes durable rules without prompt engineering.
- Approved insights inject into every new agent's `CLAUDE.md`, automatically.
- Pack Insights ritual at end of day. Agents share what they learned at the campfire, you approve what sticks, tomorrow's pack wakes smarter.
- Rules export and import. Share your team's mined rules with a teammate so they skip the months of accumulated work.

### The world
- Earthbound-strange theming. Cozy on the outside, weird underneath.
- Curated themes. Pick the register that fits your day.
- Live weather and sprite moods that shift with your local time.
- Repo cartograph. Maiko maps your codebase so agents start with context.
- Daily home overview, generated for you.

### The plumbing
- Unified AgentJob execution model. Every agent run (manual, automated, skill-driven) goes through one path.
- Automations. iPhone-style "when X happens, do Y" rules. No LLM in the trigger layer, just predicates.
- Custom skills, plugin-defined.
- Plugin architecture. Drop a `.py` file in `~/.maiko/plugins/` to wire up anything.
- Repo checks (`check_code`). Mechanical verdict before an agent says it's done.
- Model + runtime routing. Pick which model and which runtime (headless Claude, interactive Claude in tmux, or a local Ollama-served model) handles which kind of work — internal tasks like the scene note default to local; coding agents stay on Claude.

### Built-in integrations
- GitHub
- Linear
- Calendar
- PagerDuty

### Stays yours
- Runs locally on your laptop. Nothing leaves your machine.
- AGPL v3 license. Anti-SaaS-extraction.
- No telemetry, no hosted account, no cloud.

![Reviewing an agent's diff, in-app](docs/screenshots/review.png)

## About

Planet Maiko is named lovingly after my real dog Maiko.

I made this as one person in my free time for fun, sorry if it is buggy or is bad.

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- `gh` CLI (optional, for GitHub integration)
- [Claude Code](https://docs.claude.com/en/docs/claude-code/quickstart) (required for agent features)

### Install

```bash
git clone https://github.com/bkawa-bot/planet-maiko.git
cd planet-maiko

# Backend
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .

# Frontend
cd frontend && npm install && cd ..

# Agent channel (optional, MCP-backed agent comms; the `maiko` CLI
# now covers the same operations and is the default since 5/14).
# Skip this if you don't need the MCP path.
cd channel && npm install && cd ..
```

> **Mac users:** If you see SSL errors with Linear or other integrations, run `pip install --upgrade certifi`, then `open /Applications/Python\ 3.12/Install\ Certificates.command`.

### Run

Two terminals:

```bash
# Terminal 1, backend (port 8420)
source .venv/bin/activate
maiko serve

# Terminal 2, frontend (port 5173)
cd frontend && npm run dev
```

Open **http://localhost:5173** and walk through the setup wizard.

**Full guide** (mental model, architecture, plugin system, extending, CLI reference): see [`docs/GUIDE.md`](docs/GUIDE.md).

## License

Planet Maiko is [AGPL v3](LICENSE). In plain English:

- **Use it anywhere, solo or inside a company.** No strings.
- **Modify it for your team's own use.** AGPL asks that you share your source with anyone who uses your instance. When "anyone" means your coworkers, pointing them at your internal branch is enough. You don't have to publish anything to the world.
- **Build a paid product on top of it?** You must share your modifications under AGPL too. That's the anti-extraction intent. If someone commercializes Maiko, the community gets the improvements back.

Not legal advice, just the intent. If you're using Maiko to help yourself or your team, you're free. If you're selling it, share back.

Created by [Brigitte Kawaguchi](https://github.com/bkawa-bot) · Built with Claude
