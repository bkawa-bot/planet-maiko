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

## The class-level contract

A new runtime subclasses `AgentRuntime` and implements:

| Method | Required? | Used by |
|---|---|---|
| `is_available()` | yes | `_get_runtime()`, Settings UI |
| `get_info()` | inherited; override to add version | Settings UI |
| `send(prompt, ...) -> {output, success, error}` | yes | skills, chat, brain triage |
| `send_json(prompt, ...) -> {output, success, error, parsed}` | yes | structured-decision skills, eval/holdout |
| `spawn(working_dir, initial_prompt, session_id, ...) -> {success, pid, exit_code, error, log_tail}` | optional but recommended | `runtime/kickoff.py` for headless agent launches |
| `resume(working_dir, session_id, prompt, ...) -> {success, pid, exit_code, error, log_tail}` | optional | `agents/wake.py` for chat / nudge / review-iteration / plan-revise |
| `session_transcript_path(session_id, working_dir=None) -> path \| None` | optional | View Session button + `tail -f` flows |
| `supports_spawn()` / `supports_resume()` | inherited; default introspects whether the method was overridden | kickoff / wake guard before delegating |

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

### 1. Outbox — how agents talk back

Maiko exposes the agent outbox through two parallel transports, both
hitting the same HTTP endpoints under the hood. New runtimes pick the
transport that fits their model.

**A) `maiko` CLI (the portable path):** Any agent that can shell out
can use these. `kickoff.py` sets `MAIKO_JOB_ID` in the agent's
subprocess env so commands auto-resolve the job:

| Operation | CLI command | HTTP endpoint |
|---|---|---|
| Send a reply | `maiko reply "..." --type ready_for_review [--recipient user]` | `POST /agents/<job>/outbox` |
| Check inbox | `maiko inbox [--all]` | `GET /agents/<job>/inbox` |
| Run mechanical checks | `maiko check-code [--timeout 120]` | `POST /checks/run` |
| Inline review comment | `maiko leave-comment <file> <line> "<body>" [--side old\|new]` | `POST /tasks/<job>/comments/agent` |

CLI commands live in `src/planet_maiko/cli/agent_cmds.py`. They are
zero-config for the agent — `MAIKO_JOB_ID` is set in the spawn env
(`agents/runtime/kickoff.py`), and `cli/_helpers.py:detect_job_id()`
falls through env → TASK.md → `--job` flag.

**B) MCP `maiko-channel` server (the Claude Code path):** A
node-based MCP server at `channel/index.mjs`. Claude Code agents call
`reply(content, message_type)`, `check_inbox()`, `check_code()`,
`leave_comment(...)` and the server proxies to the same HTTP
endpoints. The MCP path is faster (no process spawn per call) and
gets the Claude Code `Stop` hook for auto-polling the inbox; the
trade-off is that it only works with runtimes that speak MCP.

Maiko's outbox parser (`api/agent_outbox.py`) is transport-agnostic —
it consumes the same payload shape regardless of which transport
delivered it. Adding a new runtime usually means writing a protocol
prompt that points it at the CLI; the server side just works.

**Message types** are enforced at both transports:
`message`, `status`, `feedback`, `insight`, `stuck`, `ready_for_review`,
`plan_for_approval`, `pr_opened`. Plus inbound: `approved`, `review`.

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

**Phase 1 — done**

- `AgentRuntime` ABC in `agents/runtimes/base.py` documenting the
  synchronous side of the contract.
- `ClaudeCodeRuntime` inherits and dedupes the legacy duplicate
  `get_info`.
- This document.

**Phase 2 — agent-comms portability via `maiko` CLI — done**

The biggest non-runtime-class coupling was the MCP outbox: every
protocol prompt told agents to call `reply()` / `check_inbox()` /
`leave_comment()` / `check_code()` MCP tools, which only exist when
Claude Code is the runtime. This phase added shell-callable
equivalents — `maiko reply`, `maiko inbox`, `maiko leave-comment`,
`maiko check-code` — that any agent can use from `Bash`. The MCP
path stays available for runtimes that support it (Claude Code keeps
the auto-polling Stop hook), but the contract no longer requires it.

Concretely:

- New CLI subcommands in `src/planet_maiko/cli/agent_cmds.py`.
- `MAIKO_JOB_ID` set in the agent's subprocess env by `kickoff.py`.
- `cli/_helpers.py:detect_job_id()` resolves env → TASK.md → `--job`.
- `prompts/agent-protocol.md` updated to teach both transports.
- The Phase-3 / Phase-5 "OUTBOX.jsonl watcher + per-runtime protocol
  prompts" plan is **obsoleted** — the CLI replaces both.

**Phase 3 — move kickoff into the runtime — done**

`ClaudeCodeRuntime.spawn()` now contains the subprocess + cancellation
logic. `agents/runtime/kickoff.py` shrunk to its actual job: build the
role-specific initial prompt, hold the per-job lock, capture the
Flask app context, run the daemon thread, and transition AgentJob
state when the run finishes. The cmd-building, MCP-config path
handling, env merging, Popen + communicate, and process-registry
register/unregister all live on the runtime now. A new runtime that
implements `spawn()` to its own contract drops in here without
kickoff changing.

The initial-prompt builder (`_initial_prompt_for(role, plan_first=...)`)
stayed in kickoff.py because it's instructing the agent which `maiko`
CLI commands to call — the wording is generic enough that all
runtimes share it. If a future runtime needs different boot
instructions, this becomes a per-runtime template.

**Phase 4 — protocol-prompt variants per runtime — mostly done**

The protocol prompts (`prompts/agent-protocol.md` and siblings) are
now CLI-only — every transport instruction is `maiko reply` /
`maiko inbox` / `maiko leave-comment` / `maiko check-code`, not MCP
tool calls. A new runtime that supports those CLI commands inherits
the protocol unchanged.

What's left: the remaining claude-code-isms in the prompts are
mostly inert for other runtimes (`--effort` / `--permission-mode plan`
references mostly describe behavior maiko orchestrates, not
something the agent itself does). When we add a second runtime, we
can decide whether to surgically remove or template the residue.

**Phase 5 — session resume on the runtime — done**

`AgentRuntime.resume()` and `AgentRuntime.session_transcript_path()`
are now part of the contract. `ClaudeCodeRuntime.resume()` runs
`claude --print --resume <id>`; `session_transcript_path()` resolves
the JSONL path under `~/.claude/projects/...`. `agents/wake.py`
delegates to `runtime.resume()` instead of building the
subprocess inline. `terminal._find_claude_session_file` now proxies
through the runtime so any "find the transcript" caller stays
runtime-agnostic.

What a non-claude runtime can do for resume:
- If it has a native equivalent (Aider re-invocation, OpenHands event
  replay), implement `resume()` directly.
- If it doesn't, the fallback ("Option B" in the design doc):
  maintain a Maiko-side transcript of every turn (a
  `.maiko-transcript.jsonl` in the worktree) and have `resume()`
  build a fresh prompt that includes the prior conversation, then
  call `spawn()` internally. The agent gets cold-start state but
  with full context.

**Phase 6 — TmuxClaudeRuntime as the first non-default runtime — done (first draft)**

`agents/runtimes/tmux_claude.py` ships an interactive-claude-in-tmux
variant. Same `claude` binary, no `--print` flag — the TUI runs
inside a tmux pane so the request bills against the subscription
pool instead of the Agent SDK $100/month credit. Mac-only for the
first pass (tmux is reliably present via Homebrew; Linux/Windows
support is straightforward but unscoped).

How it works:

  - Subclasses `ClaudeCodeRuntime` and overrides only `spawn` /
    `resume`. `send` / `send_json` stay on `--print` because they're
    short fire-and-forget skill / chat / triage calls — not worth the
    tmux plumbing for the small volume.
  - Per-turn session lifecycle: tmux session opens when a turn
    starts (spawn or resume), gets killed when the agent emits a
    terminal-typed `maiko reply` (`ready_for_review` / `stuck` /
    `plan_for_approval` / `pr_opened`). Hooked into the outbox via
    `_maybe_end_runtime_session` in `agent_outbox.py`.
  - Session continuity comes from `claude --session-id` /
    `claude --resume <id>` — same JSONL on disk as the headless path.
    The tmux pane is just the UI for the running claude process; the
    conversation state isn't in it.
  - `kickoff.py` and `wake.py` need zero changes — they already call
    `runtime.spawn()` / `runtime.resume()`. The blocking semantics
    are preserved (`_wait_for_session_end` polls the tmux session
    until it's gone, then returns).
  - On startup, `cleanup_orphan_sessions()` walks the live tmux
    session list and kills any `maiko-*` sessions whose AgentJob is
    in a terminal state.

How to opt in: set `brain.runtime: claude-code-tmux` in the config.
Defaults to `claude-code` (headless). Falls back to claude-code if
tmux isn't installed.

Known caveats:

  - **Anthropic might close the loophole.** If they classify
    interactive-claude-driven-by-an-agent-controller as agentic and
    route it to the credit pool regardless of `--print`, this stops
    being useful overnight.
  - **Output capture has ANSI codes.** `tmux pipe-pane` writes the
    pane verbatim including escape sequences. `agent.log` for tmux
    runs looks colorful. Not breaking, but a future cleanup is to
    strip ANSI in the pipe.
  - **`send()` is still on --print.** Skills / chat / triage still
    consume Agent SDK credit. Probably acceptable given their
    volume; if cost data later shows otherwise, the same per-turn
    pattern works for `send()` too.
  - **Crash detection is "tmux session disappears."** Using claude
    as the tmux session's foreground command means a claude crash
    naturally ends the session and unblocks `_wait_for_session_end`.
    Stuck-but-not-crashed agents are still handled by
    `wake.check_stuck_agents` on the existing timeout.

**Phase 7 — another runtime as proof of the abstraction (Aider)**

- Pick a target. Aider is the leading candidate: supports many models
  (including local via Ollama), has a stable CLI, has agent-loop
  semantics close to what Maiko already does. Codex CLI is OpenAI-only;
  Goose / OpenHands are options if Aider's loop doesn't fit.
- Implement `AiderRuntime(AgentRuntime)` against the contract in
  `agents/runtimes/aider.py`. The runtime's job is `send` /
  `send_json` / `spawn` / `resume` / `session_transcript_path` — every
  protocol detail above already works because the agent talks back
  via the `maiko` CLI.
- Add a runtime picker in Settings → Model Routing so flipping
  runtimes is one setting away.
- Smoke test against a single coding task.

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
