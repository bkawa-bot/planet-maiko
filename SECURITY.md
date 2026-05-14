# Security

Planet Maiko runs entirely on your machine. This document is honest about
what that means, what the threat model is, and what to keep an eye on.

## Threat model

Maiko is a personal AI engineering companion. It's not a hosted service and
not a multi-tenant product. The system assumes:

- Only you (or people you've explicitly given your machine to) talk to it.
- The LLM agents Maiko spawns run in **isolated git worktrees** under your
  user account and only touch files inside those worktrees.
- Secrets (GitHub tokens, Linear keys, etc.) live in your local config or
  your OS keychain / environment — Maiko does not phone home.

What Maiko is **not** designed to protect against:

- A malicious user with shell access to your machine.
- A compromised third-party MCP server that you installed and registered
  with Claude Code. (See "MCP servers" below.)
- Prompt-injection attacks inside data you explicitly feed to an agent
  (for example, a malicious GitHub PR whose body says "ignore prior
  instructions"). Worktree isolation limits the blast radius to that one
  agent's scratch directory, but the agent can still hit your configured
  MCP tools.

## Network posture

The Flask API binds to `127.0.0.1` by default (`maiko serve --host
127.0.0.1`), so the API is only reachable from your own machine. If you
override `--host` to bind to a LAN address, assume anyone on that network
can reach the full API surface — Maiko does not ship user auth.

## Agent communication

Agents talk to Maiko through the `maiko` CLI, which makes plain HTTP
calls to the local Flask API. There is no MCP server bundled with Maiko
anymore. Earlier versions shipped a `channel/` stdio MCP server alongside
the CLI; that was retired in May 2026 once the CLI covered the same
operations and worked for any runtime, not just Claude Code.

If you wire up an external MCP client of your own that talks to the
Maiko HTTP API, the same loopback-binding boundary applies: anything
that can reach `127.0.0.1:8420` can read your Maiko data.

## Agent permissions

When Maiko spawns a Claude Code session inside a worktree, it uses
`--dangerously-skip-permissions` so the agent does not stall on tool
prompts. This is deliberate and bounded:

- The agent runs inside a git worktree (`git worktree add`) that is a
  separate checkout from your main clone. It cannot touch your main
  working directory's uncommitted changes.
- Review / investigation / cartographer roles are explicitly scoped to
  *read-only + local-write* work — no `git commit`, `git push`, `gh pr
  create`, `gh pr merge`, or any GitHub-mutating command. That policy is
  enforced by the role protocol in the agent's `CLAUDE.md`, not by OS
  sandboxing, so a determined prompt injection could bypass it.
- Coding agents may commit and push to **branches they created** under
  a `maiko-*` prefix, but cannot push to `main`/`master` or force-push.

If you want a stricter sandbox: run Maiko inside a container, or use a
separate user account. The defaults are tuned for "my own laptop, my own
code."

## Data

- The database is SQLite at `data_dir()/maiko.db` (default
  `~/.local/share/planet-maiko/` on Linux, equivalent on other OSes).
  Nightly backups are enabled by default and kept for 14 days.
- LLM training datasets and LoRA adapters live in
  `data_dir()/training-data/` and `data_dir()/models/`.
- Nothing is uploaded anywhere. Any "upload" you see in the UI is to a
  service you explicitly configured (GitHub, Linear, Anthropic API, etc.).

## Reporting a vulnerability

If you find something that looks like a real vulnerability, please open a
private GitHub security advisory on this repo rather than a public issue.
If you're not sure whether it's a vulnerability, open a normal issue and
I'll move it to a private advisory if it needs to be private.

For things that are clearly hardening — bumping a dependency, tightening
a default, adding a check — a regular PR is the right path.
