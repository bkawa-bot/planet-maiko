# Planet Maiko
> These alien dogs want to live in your computer, which is a strange thing for software to want to do. Would you let them in?

![Profiles](docs/screenshots/profiles2.png)
[Testimonials (real)](https://bkawa-bot.github.io/planet-maiko/testimonials.html)

## What is Planet Maiko? 
Planet Maiko is your developer home base on a strange little planet, where your pack of space dog agents live and work alongside you. It is not just an agent orchestration platform, but a world you build and call your own.

![Home](docs/screenshots/home.png)

Features:
- **Pulls your whole stack into one place.** Data from across your tools in a centralized view.
  - Current integrations: GitHub, Linear, PagerDuty, Calendar
- **All the agent orchestration you need** but it is way cooler because the agents are space dogs:
  - Runs coding agents in isolated git worktrees
  - Custom agent builder
  - n8n-esque visual typed agent workflow builder
- **Memory that learns.** A local RAG system that builds guidelines from your past GitHub history, so agents stop repeating old mistakes.
- **Model-agnostic.** Use whatever model you want *(slightly WIP / still testing)*.
- **Your own world.** Theme designer, weather and seasons, agents with profiles and personalities.
- **Custom plugin architecture**
- **Completely local and free. No telemetry.**

#### - If it doesn't do something you need it to do, please tell me!! (seriously I have no life)

## In-app diff review, agent chat view (no terminal needed), cost-aware model routing, automations, local RAG embeddings, and more!
![Diff](docs/screenshots/diff2.png)
![insights](docs/screenshots/insights2.png)
<img width="597" height="483" alt="Screenshot 2026-05-19 at 9 41 02 PM" src="https://github.com/user-attachments/assets/5463d146-b8ac-4c57-9af9-abf8591a1008" />

### And it includes this weird guy
![Biolumen](docs/screenshots/biolumen.png)

## Install
(You probably need python 3.10+ and Node.js 18+ or else it might implode)
```
git clone https://github.com/bkawa-bot/planet-maiko.git && cd planet-maiko && python3 bootstrap.py
```

## Boot
```
maiko up
```

**[Full feature list](docs/FEATURES.md)**

**Full guide** (mental model, architecture, plugin system, extending, CLI reference): see [`docs/GUIDE.md`](docs/GUIDE.md).

## About

> *IMPORTANT UPDATE:* [The dogs are having a popularity contest and are threatening to start mining bitcoin on my computer if you don't vote](https://bkawa-bot.github.io/planet-maiko/popularity.html)

Planet Maiko is named lovingly after my real dog Maiko.

I made this as one person in my free time for fun, sorry if it is buggy or bad.

Created by [Brigitte Kawaguchi](https://github.com/bkawa-bot)
