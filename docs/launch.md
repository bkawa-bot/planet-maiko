# Launch copy

Single source of truth for how Planet Maiko gets described, in any
context. Pull from here when posting, replying, drafting, or
polishing. The README has the long-form version of all of this; this
file is the *clipboard* — pre-cut variants ready to paste, plus the
guardrails that keep voice consistent.

The README at the repo root is in good shape. This doc fills the
gaps: social-share variants, tagline placement rules, an anti-thesis
cheat sheet for "how is this different from X?", and a capture list
for the screenshots / GIFs we still want to record.

---

## Tagline

> **strange agents, strange world.**

Three words. Lowercase. The full stop is part of the punchline — it
makes the line land like a closing thought rather than a slogan.

**Use the tagline:**
- Under the project name in the GitHub repo description
- As the closing line of a pitch
- On the splash screen of the desktop app
- Opening line of a tweet or a post

**Don't:**
- Title-case it (`Strange Agents, Strange World` reads like a blockbuster).
- Pair with corporate adjacency (*"a strange new way to ship faster"*
  defeats the point).
- Translate without a native ear — the parallel rhythm is the whole
  thing.

---

## Pre-cut copy

### One-liner (≤140 chars)

> Planet Maiko is a self-hosted pack of AI agents that handles your
> engineering work — review, investigation, refactors — and treats
> you like a person while doing it.

### 30-second pitch (one paragraph, friend asks "what are you working on")

> Most AI coding tools want you to ship more, faster, harder. Planet
> Maiko is the opposite — a quiet pack of agents that runs on your
> laptop, learns your team's taste from every merged PR, and treats
> your workday like something worth coming home from. No swarms, no
> streaks, no leaderboards. Cozy on the surface, AGPL underneath.
> Strange agents, strange world.

### HN-style intro (submission body)

> I got tired of babysitting AI coding tools. Each new "10x your
> output" thing wanted me to feed it context, review its output,
> mediate conflicts between sibling agents, and pretend the cost
> wasn't there. So I built Planet Maiko: a self-hosted pack of agents
> that runs locally, picks up your stack and conventions automatically,
> learns from every approved PR review, and stays out of your way the
> rest of the time. The UI is closer to Animal Crossing than to a
> SaaS dashboard — deliberately. AGPL, no telemetry, no hosted
> account. The only subscription is caring about your tools.

Title options:
- **Show HN: Planet Maiko — self-hosted AI agent pack that learns from your PR reviews**
- **Show HN: Planet Maiko, a quiet pack of agents for your laptop**

### Tweet — short

> Planet Maiko is out. self-hosted ai agent pack that learns your
> team's taste from every merged PR. runs on your laptop, no
> telemetry, AGPL. strange agents, strange world 🐾

### Tweet — thread opener

> for the last few months i've been building a self-hosted ai
> coding companion that's the opposite of every "10x your output"
> tool: cozy, slow, learns from your team's actual PR reviews,
> doesn't try to optimize you. it's called Planet Maiko and it's
> live on github 🐾👇

(Then 4-5 follow-ups: the anti-thesis bullets, the campfire ritual,
the install one-liner, the AGPL stance, a closing screenshot or GIF.)

### GitHub repo description (≤350 chars, top of github.com/…/planet-maiko)

> A self-hosted pack of AI agents that learns from your team's PR
> reviews. Runs locally, no telemetry, AGPL. Cozy UI, kaomoji not
> emoji, weather and a dog. Strange agents, strange world.

### Closing line / sign-off (for posts, blog, anywhere a single line)

> Built with Claude. Named for a real dog. AGPL forever. Strange
> agents, strange world 🐾

---

## Anti-thesis cheat sheet

When someone asks *"how is this different from \<some popular AI
coding tool\>"*, lead with **what we deliberately didn't build** —
that's the moat. Frame as our choices, not as critiques of theirs.

**We don't have:**

- **Leaderboards / streaks / velocity dashboards.** No metric to game.
  The pack isn't trying to make you more productive; it's trying to
  give you space to think.
- **A swarm.** One pack, one conductor — you. Siblings coordinate via
  A2A conflict detection so the second one doesn't silently undo the
  first.
- **A SaaS.** Nothing leaves your machine. No telemetry, no hosted
  account, no logging in to someone else's server.
- **Prompt engineering as the primary surface.** Agents learn from
  approved PR reviews, not from hand-written guidelines you maintain
  forever.
- **Generic "AI assistant" vocabulary.** Agents have personality,
  archetype, and tribal knowledge. They're a *pack*, not a fleet.

**We lean into:**

- **Wellbeing as a first-class concern.** "Enough for today" closing
  card. Weekend mode that actually quiets the agents. Interruption
  budget that softens the voice when it's been a hard day.
- **Pixel sherbet aesthetics.** Animal Crossing / Earthbound register.
  Live weather and time-of-day backgrounds. 14 themes, all named for
  what they evoke (`midnight violet`, `slime garden`, `cherry
  blossom`) rather than light/dark/system.
- **Self-improving via real signal.** Per-repo LoRA adapters trained
  from approved Learnings. The pack you've trained for a month is
  meaningfully sharper than tomorrow's pack trained from scratch.
- **Local + AGPL forever.** Anti-extraction by license, not by
  marketing. Can't be acquired and repriced.

---

## Screenshot + GIF capture list

What we still want to record next time we're using the app, for
posts / README / a future landing page. Save under
`docs/screenshots/`. 1280×800 for full screens, smaller crops for
inline use.

**Stills:**

- [ ] **Home overview** — rolling daily greeting + focus list + memos
  pane. (Existing `home.png` may need a recapture on the new themes.)
- [ ] **Active agents** — speech-bubble cards with lifecycle dots,
  one card mid-conversation.
- [ ] **Theme switcher** — dropdown open showing the 14 grouped
  themes.
- [ ] **Diff review** — the rules-considered chip + verdict banner
  + inline review comments. (Existing `review.png` may also need
  recapture.)
- [ ] **Brain / Knowledge** — pending learnings list with one
  expanded showing provenance drilling.
- [ ] **Stuck agent reply** — memo with inline reply box.
- [ ] **Setup wizard** — fresh-install first-run flow.
- [ ] **Closing card** — "enough for today" pane near workday end.

**GIFs:**

- [ ] **Campfire** — the end-of-day pack insights ritual (already
  used in README, but a fresh capture wouldn't hurt).
- [ ] **Assigning an agent** — task → assign → spawn → first message
  landing on the home pane.
- [ ] **Theme cycle** — fast theme-switch through 3-4 palettes.
- [ ] **Stuck → reply → wake** — agent goes stuck, user replies
  inline, agent wakes and answers.

---

## Voice notes — when in doubt

- Lowercase tagline. Title case anywhere else stays normal English.
- Kaomoji over emoji where you can (`(･ω･)`, `🐾`, `(◕‿◕)`). Per
  the existing voice rules in `prompts/voice.md`. Emojis used
  sparingly, never as bullet points.
- Avoid "AI-powered", "supercharge", "10x", "boost productivity",
  "next-generation". The voice they evoke is exactly what Maiko
  isn't.
- Don't shorthand the project name to two letters. It's *Maiko* or
  the full *Planet Maiko*.
- When in doubt, read it out loud. If it sounds like a SaaS landing
  page, rewrite.

---

## What this file is not

This is the **outward-facing voice library** — what Maiko sounds
like when describing itself to someone else. It's not the design
philosophy doc, the architecture doc, or the strategy brief. Those
are separate concerns.
