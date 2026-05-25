# Planet Maiko

> *These alien dogs want to live in your computer, which is a weird thing for software to want to do. Would you let them in?*

Planet Maiko is an all-encompassing free local dev tool made by 1 dev, for other devs, for fun!

[Testimonials (real)](https://bkawa-bot.github.io/planet-maiko/testimonials.html)

![Profiles](docs/screenshots/profiles2.png)

## I don't want to read all this, I just want the dogs
Understandable, here you go! (You probably need python 3.10+ and Node.js 18+ though or else it might implode)

```
git clone https://github.com/bkawa-bot/planet-maiko.git && cd planet-maiko && python3 bootstrap.py
```

## What does Planet Maiko do? 

Planet Maiko's goal is to be the center of your workday as a developer. It pulls info in from all your external and internal tools, task managers, issue trackers, working agents etc into one system.

It has sophisticated agent orchestration with a local RAG system and (EXPERIMENTAL) model fine-tuning so you don't need a different tool for that stuff either.

> *IMPORTANT UPDATE:* [The dogs are having a popularity contest and are threatening to start mining bitcoin on my computer if you don't vote](https://bkawa-bot.github.io/planet-maiko/popularity.html)
## Why did I make Planet Maiko (what problem does it solve)?

As a professional software engineer, I was suffering a lot of mental fatigue from constantly switching between tools, all with their own agents and notifications yelling at me, while also trying to babysit a kindergarden class of baby agents.

I made Planet Maiko because I wanted 1 tool that I could trust to handle all the noise so I could focus on actually building cool things!

## What would my day look like on Planet Maiko?
![Home](docs/screenshots/home.png)

You wake up and see a greeting from Maiko (my irl dog) that knows all your in-progress work, what your manager asked you to do last week, if your teammate is waiting on a PR review, if any of your agents are stalled or stuck, even if it is a cool rainy day or a summer scorcher (in which case she will recommend you to take the afternoon off and go to the beach.)

She will then recommend a small set of action items to get your day started, pulling from your github, slack, calender, working agents, etc and distilling it down into what matters at that moment.

From there, you can live your whole day in Planet Maiko, from reviewing your team's code, to managing and chatting with multiple agent sessions, to updating your issues and tasks, the sky is the limit!

## Quick feature overview (non-exhaustive!):
### - Pulls in data from your whole stack into a centralized system.
### - Handles all your agent orchestration needs, but is way cooler cause the agents are space dogs.
### - Does some cool memory stuff like creating a local RAG system with guidelines based on your previous gh history.
### - Works with whatever model you want (slightly wip/still testing)
### - Custom automations (stuff like: my manager slacked me, set off an investigation agent to reply with an excuse for why I am OOO (just kidding but you can do this if you want))

## - If it doesn't do something you need it to do, please tell me!! (seriously I have no life)

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
- Open source, free forever, no paid tiers or subscriptions.

## In-app diff review, agent chat view (no terminal needed), cost-aware model routing, automations, local RAG embeddings, and more!
![Diff](docs/screenshots/diff2.png)
![insights](docs/screenshots/insights2.png)
<img width="597" height="483" alt="Screenshot 2026-05-19 at 9 41 02 PM" src="https://github.com/user-attachments/assets/5463d146-b8ac-4c57-9af9-abf8591a1008" />

## Most importantly: agents are weird alien dogs, cause why not?

![Biolumen](docs/screenshots/biolumen.png)

**[Full feature list](docs/FEATURES.md)**

## Install

### Prerequisites

- Python 3.10+
- Node.js 18+
- [`gh` CLI](https://cli.github.com)

### Setup

```bash
git clone https://github.com/bkawa-bot/planet-maiko.git && cd planet-maiko && python3 bootstrap.py
```
> **SSL errors** with Linear or other integrations? `pip install --upgrade certifi`, then `open /Applications/Python\ 3.12/Install\ Certificates.command`.

### Next time

```bash
maiko up
```

**Full guide** (mental model, architecture, plugin system, extending, CLI reference): see [`docs/GUIDE.md`](docs/GUIDE.md).

## About

Planet Maiko is named lovingly after my real dog Maiko.

I made this as one person in my free time for fun, sorry if it is buggy or bad.

Created by [Brigitte Kawaguchi](https://github.com/bkawa-bot)
