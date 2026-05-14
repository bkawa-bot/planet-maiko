# Agent Runtime Contract

Maiko was scaffolded as runtime-pluggable from the start (the
`runtimes/` package, the `_get_runtime()` indirection in
`brain_session.py`), but only `ClaudeCodeRuntime` ever shipped. With
Anthropic splitting agentic from interactive usage in mid-2026, the
codebase needs to be ready to swap or supplement claude-code with
another backend.

This document captures what a new runtime has to satisfy. It is a
**snapshot of the coupling**, not just an interface definition — the
`AgentRuntime` ABC in `agents/runtimes/base.py` covers the API surface
of the runtime class, but real model-agnosticism also requires
handling the protocols that ride on top of it.

## The shape at a glance

```
┌─────────────────────────────────────────────────────────────┐
│ Maiko (Python)                                              │
│                                                             │
│   brain cycle, skills, chat, eval                           │
│      │                                                      │
│      ▼                                                      │
│   _get_runtime()  ─►  AgentRuntime  ─►  ClaudeCodeRuntime   │
│      │                                       │              │
│      │                                       ▼              │
│      │                                  `claude` CLI        │
│      │                                       │              │
│      │                                       ▼              │
│      │                                  Anthropic API       │
│      ▲                                                      │
│      │                                                      │
│      └── MCP outbox HTTP webhook ◄─── agent calls reply()   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

Two invocation patterns coexist today:

1. **Synchronous, one-shot.** `runtime.send(prompt)` blocks until a
   single response comes back. Used by skills, chat, brain triage, and
   model judgments in `eval/`. Handled entirely by the runtime class.

2. **Asynchronous, agent process.** `kickoff_agent_headless()` in
   `agents/runtime/kickoff.py` spawns a long-running `claude` process
   that drives multi-tool work in a worktree. The process exits when
   the agent posts `ready_for_review` via MCP; Maiko learns about that
   through the outbox webhook, not by waiting on the subprocess.

The runtime class today covers only the first pattern. The kickoff
path still talks to `claude` directly. Migrating that into
`runtime.spawn()` is the next step (Phase 2 below).

## The class-level contract (Phase 1)

A new runtime subclasses `AgentRuntime` and implements:

| Method | Required? | Used by |
|---|---|---|
| `is_available()` | yes | `_get_runtime()`, Settings UI |
| `get_info()` | inherited; override to add version | Settings UI |
| `send(prompt, ...) -> {output, success, error}` | yes | skills, chat, brain triage |
| `send_json(prompt, ...) -> {output, success, error, parsed}` | yes | structured-decision skills, eval/holdout |
| `spawn(working_dir, initial_prompt, ...) -> {success, pid, ...}` | optional | future kickoff path |
| `supports_spawn()` | inherited; default introspects `spawn` override | kickoff fallback |

Caller contract:

- **Tolerance for unknown flags.** `send()` takes a union of every flag
  any caller has ever passed (`model`, `effort`, `permission_mode`,
  `allowed_tools`, `skip_permissions`, `session_id`). A runtime that
  doesn't understand a flag should ignore it silently, not raise.
- **Idempotent result shape.** `{output, success, error}` with optional
  `parsed`. Never raise from `send` for normal failure (process
  exit-code, timeout, parse error) — return `success=False` instead.

## The implicit coupling (Phase 2 — the actual work)

Below this layer, Maiko leans on several Claude-Code-specific things.
A real non-claude runtime needs to substitute or stub each one.

### 1. MCP outbox — how agents talk back

Maiko ships a node-based MCP server at `channel/index.mjs` (the
`maiko-channel` package). Agents call `reply(content, message_type)`
and `check_inbox()` on it; the server posts back to Maiko's HTTP
endpoint `/agents/<job_id>/outbox`. Every protocol prompt
(`prompts/agent-protocol.md`, `prompts/review-agent-protocol.md`,
etc.) instructs the agent in MCP-tool terms.

**Coupling**: tight. The agent must speak MCP, and Maiko's outbox
parser (`api/agent_outbox.py`) expects exact message_types
(`ready_for_review`, `status`, `stuck`, `pr_opened`, `plan_for_approval`,
`approved`).

**For a non-MCP runtime (Aider, Codex, local Ollama loop)**, two
plausible substitutes:

- **File-based outbox.** Agent writes JSON lines to `OUTBOX.jsonl` in
  the worktree; Maiko's brain cycle tails the file and calls the same
  handlers in `agent_outbox.py`.
- **HTTP webhook from the agent loop itself.** If the runtime is a
  Python library (not a subprocess CLI), it can call into Maiko
  directly. Same handlers, different transport.

Either way, the protocol prompts have to be re-templated per runtime
so the agent gets the right "this is how you report back" instructions.

### 2. The Stop hook — inbox polling

`scaffold.py:483-487` installs a Claude Code `Stop` hook that, on
every "agent is about to finish" event, polls the maiko inbox for
unread messages and blocks-with-inject if there are any. This is what
makes the in-app chat with agents work: the user types a follow-up,
Maiko queues it on the inbox, the next time the agent settles it
picks up the message and keeps going.

**Coupling**: tight. The Stop hook is a Claude Code feature.

**For a non-claude runtime**, the substitute is polling. The agent
loop checks `INBOX.jsonl` (or hits an HTTP endpoint) every N turns
and processes any pending messages before settling. Less elegant but
fully functional.

### 3. CLAUDE.md as the prompt format

`agents/runtime/scaffold.py:37-172` assembles a `CLAUDE.md` file in
the worktree containing:

- The role-specific protocol (`agent-protocol.md`,
  `review-agent-protocol.md`, `investigation-agent-protocol.md`,
  `cartographer-agent-protocol.md`)
- Team `role_instructions` from `config.agents.role_instructions`
- The character block (name, tagline, archetype guidance)
- Active Insights (team playbook bullets, scoped to repo or global)
- The agent's personal bio (`profile.instructions`)
- Optional Specialty (the CustomSkill prompt for this run)

**Coupling**: medium. CLAUDE.md is a Claude-Code convention but the
content is plain markdown. Aider uses `CONVENTIONS.md`. Codex uses
the system prompt directly. Local-model loops would synthesize this
into a system message.

**For a new runtime**, the runtime knows where to put its system
context. The brief assembler in scaffold.py would take a runtime
argument and emit the file at the runtime's chosen path/name.

### 4. Session persistence

Claude Code saves transcripts at
`~/.claude/projects/<escaped-path>/<session_id>.jsonl`. The "View
Session" link on the job page reads from there.
`agents/runtime/kickoff.py` passes `--session-id` so Maiko knows
where to find it later.

**Coupling**: medium. Only matters for the "view full transcript"
feature.

**For a new runtime**, either point at the runtime's own session
storage (Aider has `.aider.chat.history.md`, OpenHands has its own
event log), or accept that the View Session button is claude-only and
hide it for other runtimes.

### 5. Flag semantics — model / effort / permission_mode

- **`model`** maps to `--model haiku|sonnet|opus` in claude-code. Other
  CLIs use different names (`--model gpt-4o`, `--main-model
  anthropic/claude-3-opus`, etc.). `agents/routing.py` already
  centralizes this — adapter per runtime is the cleanest fix.
- **`effort`** maps to `--effort low|medium|high|max` (Claude Code's
  reasoning-budget flag). No equivalent in most other CLIs. Runtimes
  should silently ignore.
- **`permission_mode="plan"`** maps to `--permission-mode plan`, which
  restricts the tool set to read-only (Read / Glob / Grep / Bash without
  writes). Equivalent in Aider: `--read-only`. Equivalent in others:
  runtime-specific. The runtime should map "plan" to whatever its
  read-only restriction is, or ignore if there's none.
- **`skip_permissions=True`** maps to
  `--dangerously-skip-permissions` — claude-code's "don't prompt for
  tool use" for autonomous runs. Most other agent CLIs don't prompt
  in the first place; runtime can ignore.

### 6. Tool allowlists + global MCP discovery

`ClaudeCodeRuntime._get_allowed_tools()` reads
`config.brain.allowed_tools` and merges in every MCP server registered
in `~/.claude.json` (both global and per-project). The result feeds
`--allowedTools` flags on each `send()`.

**Coupling**: very tight. The discovery is reading claude-code's
config file directly. Other runtimes have their own tool models
(Aider has a fixed set, Codex uses OpenAI function calling, etc.).
This whole subsystem is claude-only and should be moved into
`ClaudeCodeRuntime`'s implementation rather than the abstract
interface.

## Migration plan

**Phase 1 — done (this commit)**

- `AgentRuntime` ABC in `agents/runtimes/base.py` documenting the
  synchronous side of the contract.
- `ClaudeCodeRuntime` inherits and dedupes the legacy duplicate
  `get_info`.
- This document.

**Phase 2 — move kickoff into the runtime**

- Add `ClaudeCodeRuntime.spawn(working_dir, initial_prompt, ...)`
  that contains today's `kickoff.py` logic.
- Keep `agents/runtime/kickoff.py` as a thin caller that delegates to
  `runtime.spawn()` and handles the database / state bookkeeping.
- Now the entire "launch an autonomous agent in a worktree" flow
  goes through `runtime.spawn()`. A new runtime can implement it
  however its underlying tool works.

**Phase 3 — protocol pluggability**

- Move the protocol-prompt loading
  (`prompts/agent-protocol.md`, etc.) and the brief assembly into
  per-runtime templates. Claude Code keeps the current `reply()` /
  `check_inbox()` MCP-tool instructions; an Aider runtime gets a
  variant that says "write a line to OUTBOX.jsonl with this shape
  when you're done."
- Replace the Stop-hook inbox-polling with an explicit poll loop the
  agent runs every N turns. Claude Code can still use the Stop hook
  internally (cleaner UX), but the *contract* with Maiko stops
  depending on it.

**Phase 4 — second runtime as proof**

- Pick a target. Aider is the leading candidate: supports many models
  (including local via Ollama), has a stable CLI, has agent-loop
  semantics close to what Maiko already does. Codex CLI is OpenAI-only;
  Goose / OpenHands are options if Aider's loop doesn't fit.
- Implement `AiderRuntime` against the contract.
- Smoke test against a single coding task.
- Add a runtime picker in Settings → Model Routing.

**Phase 5 — alternate agent-comms back-channel for non-MCP runtimes**

- Stand up the file-based outbox (`OUTBOX.jsonl` watcher) as a parallel
  path. The brain cycle tails it the same way it processes MCP outbox
  posts today. Aider / Codex / local-Ollama runtimes use this; Claude
  Code keeps MCP.

## Adding a new runtime — checklist

When the abstraction is fully in place, adding a runtime should be:

- [ ] Subclass `AgentRuntime` in `agents/runtimes/<name>.py`.
- [ ] Implement `is_available`, `send`, `send_json`. Override
      `get_info` to add version.
- [ ] If this runtime can drive multi-tool agents in a worktree:
      implement `spawn()`.
- [ ] Register in the runtime registry (TODO: design — currently
      `_get_runtime()` is hardcoded).
- [ ] If the runtime doesn't speak MCP, add a protocol-prompt variant
      under `prompts/<name>/` and a back-channel handler in
      `api/agent_outbox.py` (or its replacement).
- [ ] Smoke test: run one skill, one chat exchange, one coding task,
      one review task.
- [ ] Add to the Settings → Model Routing UI.

The first runtime added against the finished contract is the real test
of whether the abstraction is right.
