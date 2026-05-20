# Planet Maiko

> *These alien dogs want to live in your computer. Would you let them in?*

**A local-first agent orchestrator where your AI agents are weird alien dogs that know a little too much.**

Free forever, no paid tiers or subscriptions. Made by one dev for other devs.

![title](docs/screenshots/title.png)

---

> **⚠️ Beta.** Planet Maiko is in active beta testing. Expect breaking changes, schema migrations, rough edges, and bugs. If you try it and something's broken, [file an issue](https://github.com/bkawa-bot/planet-maiko/issues/new).

---

We live in strange times, and yet our dev tools are so painfully un-strange. No one knows what being a software engineer will be like in a year, or even a month. If we are all headed to our own inevitable obsolescence, we might as well have fun.

![Planet Maiko home](docs/screenshots/home.png)

![profiles](docs/screenshots/profiles2.png)

## What does Planet Maiko do? 

All the required agent orchestration work. Automatically kicks off agents, lets them yell at each other (with Maiko as the mediator), agent task lifecycles, context sharing... but also some more interesting features as well such as conflict detection, (experimental) fine-tuning, self-curated shared memory and insights etc

## How is Planet Maiko different from RinkStack, Mazino.ai, or QuatroForce? (I just made all of those up)

### A self-maintaining rulebook from your team's PR history

Internal knowledge and specific gotchas get captured automatically, no manual write-ups. When an agent works on something, it only sees the handful of rules that matter for that change. You can keep a pool of hundreds of very specific nits without drowning every prompt.

```mermaid
flowchart LR
  GH["your PR history"]:::source
  SIG["raw reviewer<br/>comments"]:::signal
  RULES["clustered<br/>rulebook"]:::rules
  PACK["the pack"]:::pack
  GH -->|"scraped from merged PRs"| SIG
  SIG -->|"clustered into rules"| RULES
  RULES -->|"local RAG: only the rules<br/>that matter for this task"| PACK

  classDef source fill:#1A1F4A,stroke:#FF6FAF,stroke-width:2px,color:#F4F7FF;
  classDef signal fill:#1A1F4A,stroke:#FF8FE0,stroke-width:2px,color:#F4F7FF;
  classDef rules fill:#1A1F4A,stroke:#6FE0E8,stroke-width:2px,color:#F4F7FF;
  classDef pack fill:#1A1F4A,stroke:#C4F542,stroke-width:2px,color:#F4F7FF;
```

### The dogs confess their own mistakes

When one gets something wrong it writes down what it learned, and the whole pack reads it. Future agents inherit those notes in their preamble, so a gotcha gets discovered once.

```mermaid
flowchart LR
  DOG["a dog<br/>(after a task)"]:::pack
  NOTE["what I got wrong"]:::signal
  MEM["pack insights<br/>(shared memory)"]:::mem
  NEW["future agents<br/>inherit it"]:::pack
  DOG -->|"confesses"| NOTE --> MEM --> NEW

  classDef pack fill:#1A1F4A,stroke:#C4F542,stroke-width:2px,color:#F4F7FF;
  classDef signal fill:#1A1F4A,stroke:#FF8FE0,stroke-width:2px,color:#F4F7FF;
  classDef mem fill:#1A1F4A,stroke:#FFE66D,stroke-width:2px,color:#F4F7FF;
```

### How a task moves through the system

```mermaid
flowchart LR
  SRC["GitHub / Linear /<br/>PagerDuty / Calendar"]:::source
  PUP["Pupdates"]:::signal
  AUTO["Automations<br/>(when X, then Y)"]:::auto
  TASK["Tasks"]:::task
  AGENT["Agent in an<br/>isolated worktree"]:::pack
  PR["Branch / PR"]:::pr
  SRC -->|"pollers + plugins"| PUP --> AUTO --> TASK --> AGENT -->|"commits"| PR

  classDef source fill:#1A1F4A,stroke:#FF6FAF,stroke-width:2px,color:#F4F7FF;
  classDef signal fill:#1A1F4A,stroke:#FF8FE0,stroke-width:2px,color:#F4F7FF;
  classDef auto fill:#1A1F4A,stroke:#FFE66D,stroke-width:2px,color:#F4F7FF;
  classDef task fill:#1A1F4A,stroke:#C9A6FF,stroke-width:2px,color:#F4F7FF;
  classDef pack fill:#1A1F4A,stroke:#C4F542,stroke-width:2px,color:#F4F7FF;
  classDef pr fill:#1A1F4A,stroke:#6FE0E8,stroke-width:2px,color:#F4F7FF;
```

## Build a plugin for any tool you never want to have to look at again.

Plug in any data you need by building 1 python class. Internal tools, big names, whatever shiny new GSD task manager you are trying this week.

**Current integrations:**

- PagerDuty
- Linear
- Calendar
- GitHub

## Stays yours

- Runs locally on your laptop. Nothing leaves your machine.
- No telemetry, no hosted account, no cloud.

Open source, free forever, no paid tiers or subscriptions.

## In-app diff review, agent chat view (no terminal needed), cost-aware model routing, automations, and more!
![Diff](docs/screenshots/diff2.png)

## Most importantly: agents are weird alien dogs, cause why not?

![Biolumen](docs/screenshots/biolumen.png)

**[Full feature list](docs/FEATURES.md)**

## Install

### Prerequisites

- macOS (or Linux/Windows for development)
- Python 3.10+
- Node.js 18+
- `gh` CLI
- Claude Code (the agent runtime), installed and on your PATH

### Setup

```bash
git clone https://github.com/bkawa-bot/planet-maiko.git
cd planet-maiko

python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
cd frontend && npm install && cd ..

gh auth login    # GitHub: repo discovery + worktrees
```

> **SSL errors** with Linear or other integrations? `pip install --upgrade certifi`, then `open /Applications/Python\ 3.12/Install\ Certificates.command`.

### Run

One command:

```bash
maiko up
```

That starts the backend, starts the frontend, and opens
`http://localhost:5173` for you. Ctrl+C stops both. First run creates
its own database and config, then shows a setup wizard.

Prefer two terminals? `maiko serve` (backend, port 8420) and, in
another, `cd frontend && npm run dev` (frontend, port 5173).

> There's an experimental Tauri desktop shell (`make app`), but it's
> currently buggy. The terminal launch above is the supported path.

**Full guide** (mental model, architecture, plugin system, extending, CLI reference): see [`docs/GUIDE.md`](docs/GUIDE.md).

## About

Planet Maiko is named lovingly after my real dog Maiko.

I made this as one person in my free time for fun, sorry if it is buggy or bad.

Created by [Brigitte Kawaguchi](https://github.com/bkawa-bot)
