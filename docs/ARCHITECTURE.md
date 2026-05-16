# Planet Maiko — Architecture

*strange agents, strange world.*

This is the deep technical companion to [`GUIDE.md`](GUIDE.md). The Guide explains the
mental model for a user. This document explains how the machine actually works,
subsystem by subsystem, for someone changing the code.

A note on the shape of the thing: Maiko is a local-first Flask + SQLite backend, a
React/Vite frontend, and a thin Tauri shell that launches both. There is no cloud, no
account, no telemetry. Everything below runs on one laptop. The whole system is driven
by a single background thread (the brain cycle) ticking every few minutes, the way a
CPU clock drives a pipeline.

---

## Table of contents

1. [The big picture](#1-the-big-picture)
2. [Data and persistence](#2-data-and-persistence)
3. [The brain cycle](#3-the-brain-cycle)
4. [Pupdates: the event substrate](#4-pupdates-the-event-substrate)
5. [Automations: the when→then router](#5-automations-the-whenthen-router)
6. [Tasks, AgentJobs, and the agent lifecycle](#6-tasks-agentjobs-and-the-agent-lifecycle)
7. [A2A communication and the maiko CLI](#7-a2a-communication-and-the-maiko-cli)
8. [Knowledge: signals to learnings](#8-knowledge-signals-to-learnings)
9. [RAG retrieval](#9-rag-retrieval)
10. [LoRA self-training (parked)](#10-lora-self-training-parked)
11. [Insights and the playbook](#11-insights-and-the-playbook)
12. [Pack Insights: the campfire ritual](#12-pack-insights-the-campfire-ritual)
13. [Memos: the durable inbox](#13-memos-the-durable-inbox)
14. [Awareness: conflict detection and expertise](#14-awareness-conflict-detection-and-expertise)
15. [The home overview and the scene](#15-the-home-overview-and-the-scene)
16. [Projects driver and guardrails](#16-projects-driver-and-guardrails)
17. [Plugin architecture](#17-plugin-architecture)
18. [Frontend and desktop shell](#18-frontend-and-desktop-shell)
19. [Sharp edges and parked work](#19-sharp-edges-and-parked-work)
20. [Where to look](#20-where-to-look)

---

## 1. The big picture

Six core concepts move data through the system. Knowing which is which makes every
page and every table legible.

| Concept | What it is | Model / table | Durability |
|---|---|---|---|
| **Pupdate** | A thing to notice. One event, from a poller or from inside Maiko. | `Pupdate` / `pupdates` | Ephemeral. Auto-dismissed once routed. |
| **Task** | A thing to finish. What *you* owe. | `Task` / `tasks` | Durable. You close it. |
| **AgentJob** | A thing the pack runs. One agent session. | `AgentJob` / `agent_jobs` | Durable. The pack closes it. |
| **Memo** | A thing you need to see or decide. | `Memo` / `memos` | Durable. The canonical inbox surface. |
| **Signal → Learning** | A coding rule, distilled from feedback. | `Signal` / `Learning` | Durable knowledge. |
| **Insight** | Tribal knowledge agents inherit verbatim. | `Insight` / `insights` | Durable, optionally TTL'd. |

The flow at the highest level:

```
  POLLERS (github / linear / calendar / pagerduty)        INTERNAL EMITTERS
  + plugins                                               (agents, processor, API)
        |                                                        |
        +----------------------> PUPDATES <----------------------+
                                    |
                          BRAIN CYCLE (every ~5 min, 16 phases)
                                    |
        +-----------------+---------+----------+------------------+
        v                 v                    v                  v
   AUTOMATIONS        AGENT JOBS           SIGNALS/LEARNINGS     MEMOS
   (when -> then)     (worktree + claude)  (PR feedback -> RAG)  (inbox)
        |                 |                    |                  |
        +--------+--------+----------+---------+------------------+
                 v                   v
            TASKS                SURFACES: Home overview, Pack page,
            (what you owe)       Knowledge page, Automations page,
                                 the ambient scene
```

Nothing here is a queue server or a message bus. It is one SQLite database, a set of
SQLAlchemy models, and a loop that walks them in a fixed order. The "event-driven"
feel comes from the cycle: external things become rows, the cycle reads rows and
writes more rows, and the frontend polls the result.

---

## 2. Data and persistence

**One SQLite file.** `~/.local/share/planet-maiko/maiko.db` (XDG; macOS forces
`~/.local/share`). Config is YAML at `~/.config/planet-maiko/config.yaml`. Both are
overridable via `MAIKO_DATA_DIR` / `MAIKO_CONFIG_DIR` / `MAIKO_DB_PATH`. Paths live in
`paths.py`; config in `config.py` (`DEFAULT_CONFIG` is the authoritative shape;
`load_config()` shallow-merges user values per top-level key so plugin sections
survive).

**SQLite tuned for a long-lived multi-thread process** (`database.py`). On every
connection: `journal_mode=WAL` (readers and writers do not block each other, only
writer-vs-writer contends), `busy_timeout=30000`, `synchronous=NORMAL`,
`foreign_keys=ON` (SQLite ignores FK constraints otherwise). Slow-query and slow-tx
watchdogs log a short `planet_maiko`-only stack when a query exceeds 500ms or a
transaction exceeds 2000ms. That plumbing exists because "database is locked" is the
characteristic failure mode of this design, and it needed to be diagnosable.

**Migrations are deliberately lightweight.** There is no Alembic. `create_app()` runs
`db.create_all()` (creates missing tables only), then a small idempotent pass:

- `_PATCH_COLUMNS` in `app.py` is a list of `(table, column, sql_type)`. The pass
  reads `PRAGMA table_info`, and runs `ALTER TABLE ADD COLUMN` for any missing one.
  This is the *only* supported migration for adding a nullable column to a shipped
  model. Append a tuple, existing user databases survive the next boot.
- A handful of one-shot rebuilds (`_drop_diff_comment_task_fk`,
  `_rename_diff_comment_task_to_job`, `_retro_incubate_thin_pending`) use the SQLite
  recreate-table idiom for the few destructive changes that had to happen. Each is
  idempotent and exits fast on subsequent boots.

Anything more destructive than "add a nullable column" still means a fresh database.
This is a single-user beta; that trade is intentional.

**Timestamps.** `iso_utc()` re-attaches UTC on serialize because SQLite drops tzinfo
on round-trip and browsers would otherwise read naive ISO as local time. Displayed
times are minute-resolution, never seconds.

The 16 models registered in `create_app()`: `pupdate`, `task`, `project`,
`agent_message`, `signal`, `learning`, `agent_profile`, `custom_skill`,
`diff_comment`, `insight`, `automation`, `agent_job`, `memo`, `adapter_eval`
(plus the relationships between them).

---

## 3. The brain cycle

`brain/cycle.py` is the clock. `create_app(start_scheduler=True)` (the `maiko serve`
path) spawns a daemon thread `brain-cycle` that sleeps 30s for the app to come up,
then loops: `with app.app_context(): run_brain_cycle(app)` every
`brain.cycle_interval_minutes` (default 5), with a 1-second-granularity interruptible
sleep so shutdown is responsive. A second daemon thread runs nightly DB backups.
Pollers no longer have their own threads; they are plugins fired inside the cycle.

`run(app)` walks `_PHASES` in fixed order. Each phase is its own function under
`brain/phases/`, so a failure in one is isolated. After **every** phase the
orchestrator calls `db.session.rollback()` to clear any pending write a phase leaked,
which otherwise surfaces as a `UNIQUE constraint failed` in a later, unrelated phase.

The pipeline, in order:

| # | Phase | What it does |
|---|---|---|
| 1 | `agents` | Claims `source="agent"` pupdates (heartbeats), marks them processed. Record-only; agents never auto-close tasks. |
| 2 | `auto_complete_reviews` | `pr_approved` / `pr_merged` pupdates → match review task by repo+number/URL → `task.status="done"`. |
| 3 | `awareness` | A2A conflict detection across Maiko-prepared worktrees (file/method overlap). |
| 4 | `automations` | The unified when→then engine. Cycle-scope and pupdate-scope. The primary router. |
| 5 | `pupdates` | Residual handler. Only special case: `pr_review_commented` → wake the owning agent. Else mark processed; stale low-priority auto-dismiss after 24h. |
| 6 | `synthesis` | LLM-classify one batch of unsynthesized PR-comment signals into clean rules (or delete junk). |
| 7 | `learning` | Cluster synthesized signals into Learnings; targeted drift-dedupe of the categories that changed this tick. |
| 8 | `nudge_quiet_agents` | Wake quiet agents so they can re-engage *before* stuck-check flags them. Order matters here. |
| 9 | `stuck_check` | Flag `working` agents with no activity for 15 min as `stuck`. |
| 10 | `projects` | Advance active multi-phase projects. |
| 11 | `orchestrate` | Route ready unassigned tasks to agent profiles. |
| 12 | `unblock` | Tasks whose `depends_on` are all done → `blocked` → `new`. |
| 13 | `spawn_jobs_for_tasks` | Assigned agent-runnable tasks with no active job → mint a queued `AgentJob`. |
| 14 | `execute_agent_jobs` | Take up to **2** queued jobs/cycle → prepare worktree + kick off the agent. The token-spend cap lives here. |
| 15 | `stuck_escalation` | Escalate agents that have been stuck across cycles. |
| 16 | `worktree_sweep` | Daily-gated: reclaim terminal/orphan worktrees older than the cutoff. Never touches active ones. |

After all phases, plugin hooks fire: `on_brain_cycle(phase, results, app)` once per
phase, then `on_cycle_tick(app)` exactly once. The latter is what drives all poller
plugins. `get_status()` exposes cached (5s) cycle count and pending-work counts for
the Home health pane.

The CPU metaphor is load-bearing: each tick the cycle reads the world's current state
out of SQLite, runs every "instruction" in pipeline order, and commits the next
state. There is no other scheduler.

---

## 4. Pupdates: the event substrate

A **pupdate** ("planet update") is one ephemeral event the brain should triage. It is
the unit of ingestion and the common currency every external integration speaks. It
is *not* a durable record; once an automation routes it, it is dismissed and its
lasting effect lives on a Task / AgentJob / Memo / Insight.

**Model** (`models/pupdate.py`, table `pupdates`): string PK, `source`
(`github`/`linear`/`calendar`/`pagerduty`/`agent`/`maiko`), `source_id` (the
dedup key from the source), `type` (free string), `priority`
(`low`/`normal`/`high`/`urgent`), `category` (`action` vs `activity`, computed at
insert by a `before_insert` listener from the `ACTION_TYPES` frozenset), `title`,
`body`, `url`, `actionable`, `action_hint`, `tags` (first tag is conventionally a
grouping key: task id or repo), `dismissed`, `expires_at`, `extra` (JSON, stored in
a DB column literally named `metadata`, carries repo/number/task_id/etc), and
`brain_processed` (the queue cursor: `False` = not yet routed).

State is the cross product of `brain_processed` x `dismissed`. There is no FSM column.

**Ingestion and dedup.** `MaikoPlugin.emit_pupdates()` is the central gate. It
computes a deterministic id `sha256(f"{plugin}:{source_id}")[:12]` and skips any id
that already exists. That is the entire dedup mechanism, and it is why pollers encode
mutable state into `source_id`: the GitHub poller puts the head SHA into a
review-request id and the latest-comment timestamp into a pr-comment id, so a
genuinely new event produces a new id and is not swallowed. Pollers can emit
`Signal` rows in the same transaction (for the learning system).

**`pupdate_types.py`** is a registry for the Automation editor's type dropdown only.
It does not validate the database. Built-in types span the GitHub / Linear / Calendar
/ PagerDuty / Agents / Skills / Notifications groups; plugins add their own via
`register_pupdate_types()`.

**Where they go.** The brain cycle is the only consumer. Phase 1 claims agent
heartbeats. Phase 4 (Automations) is the real router. Phase 5 (the processor) is the
last-resort handler whose one real job is waking an agent on new PR review comments;
everything else it marks processed and leaves visible, with stale low-priority items
auto-dismissed after 24h. Unrouted pupdates surface in the Brain Queue
(`GET /api/pupdates?brain_processed=false`); `category="action"` ones surface as
"waiting on you". Note the architectural trend: durable user-facing items have
migrated to **Memos**; pupdates are increasingly the internal event substrate, not a
display surface. The home overview prompt no longer sees pupdates at all (only
calendar-source ones, for today's events).

```
 external event ─► poller.poll() ─► to_pupdates() ─► emit_pupdates()
                                                        │ sha256(plugin:source_id)[:12]
                                                        │ skip if id exists  (DEDUP)
                                                        ▼
                                            pupdates (brain_processed=False)
                                                        │
                            BRAIN CYCLE ────────────────┤
                            [4 automations]  first match → run actions → dismiss
                            [5 processor]    pr_review_commented → wake agent
                                                        ▼
                              Task / AgentJob / Memo / Insight (durable)
```

---

## 5. Automations: the when→then router

This is Maiko's deterministic, user-editable rule engine, the unified replacement for
what used to be three subsystems (agent goals, a correlator, scheduled skills). The
design rule is explicit: **the engine never calls an LLM.** All intelligence lives in
the skills that actions reference by name. The trigger layer is just predicates.

**Model** (`models/automation.py`, table `automations`): `name`, `description`,
`when` (JSON list of `{kind, config}`), `when_logic` (`all`/`any`), `then` (JSON list
of `{kind, config}`, run in order), `status` (`active`/`paused`/`archived`),
`last_fired_at` + `fire_count` + `cooldown_days` (re-fire gating), `created_by`
(`user`/`seed`/`proposal`/`plugin:<name>`), `scope_repo`, and the key field
`execution_scope`.

**Two execution scopes** (`brain/automations/__init__.py:evaluate()`, brain cycle
phase 4):

- **cycle**: evaluated once per tick, gated by `cooldown_days`. For stale-overview
  watches, incident chains, cadence-driven skill runs.
- **pupdate**: the engine iterates each unprocessed pupdate (oldest first, limit
  200) and tests automations against it in id order. **First match wins**, the
  pupdate is auto-dismissed (a routed queue event has no remaining inbox value), and
  `brain_processed=True` regardless of match.

**Conditions** (`brain/automations/conditions.py`, dispatch table `CONDITIONS`):
`cadence` (every N minutes/hours), `overview_stale` (a repo's cartographer Insight
missing or older than N days), `pupdate_match` (dual-mode: tests one pupdate, or
scans recent pupdates within a window; criteria include source/type/types/
type_prefix/priority/priority_in/actionable/has_tag/title_contains), and
`pupdate_chain` (the correlator: fires when ALL of `types` appear within a window
grouped by repo or tag, e.g. CI-failed + changes-requested on the same repo → one
investigation job). Matched conditions emit a context dict that templatizes into the
action config.

**Actions** are split into execution handlers (`brain/automations/actions/`) and an
editor discovery spec (`automation_actions.py`, kept in sync). Cycle-scope handlers:
`run_agent_job` (mint an AgentJob, or a `job_approval` Memo if `ask_first`),
`create_task`, `notify_me` (emit a notification Memo), `skip` (explicit no-op to
claim a pupdate without side effects). Pupdate-scope handlers:
`spawn_agent_job_from_pupdate`, `create_task_from_pupdate` (dedupes on `(url, type)`,
links instead of duplicating), `complete_linked_task` (closes tasks + cancels jobs +
dismisses every pupdate sharing the URL when a PR closes), `dismiss_pupdate`.

**Seeding** (`brain/automations/seeding.py`, run at boot, idempotent): ~11
pupdate-scope "rule" automations (auto-dismiss CI-passing, create task on review
request / Linear assignment / incident / CI failure, close linked task on PR
merged/approved, etc.), one wildcard cycle-scope "Keep repo overviews current"
automation, and any `register_default_automations()` from plugins. Deleting a seeded
automation **archives** it instead (a hard delete would respawn it next boot).

A naming caution: "Automations" (the `Automation` model, behavioral triggers) is
unrelated to the "rules" in `rules_api.py` (RAG retrieval over the `Learning` model).
The seeded pupdate-scope automations are informally called "rules" in some docstrings
but they are `Automation` rows.

---

## 6. Tasks, AgentJobs, and the agent lifecycle

The single most important distinction in the codebase:

- **Task** = what *you* owe. Lives on the Tasks page. You (or a `pr_merged`
  automation) close it. Fields: `id`, `title`, `type` (`todo`/`bug`/`feature`/
  `review` plus orchestration roles `investigation`/`repo_analysis`), `status`
  (`blocked` → `new` → `in_progress` → `review`/`done`/`cancelled`), `priority`,
  `assigned_agent_id`, `depends_on` (JSON list of task ids; while any is unfinished
  the task stays `blocked`), `project_id`, `source_pupdate_id`, `extra`.
- **AgentJob** = what the *pack* runs. Lives on the Pack page. One agent session.
  Per the unified-jobs invariant, **every** agent session routes through an AgentJob:
  manual assignment, automation, pupdate, specialty/skill, manual UI launch. The
  only bypass is internal synchronous LLM calls.

One Task can spawn several AgentJobs (a coding job, then a review job, each with its
own worktree and diff). An agent finishing moves its **job** to `done` but the linked
**task** only to `review`, never `done`: that preserves the worktree so you can read
the diff. `AgentJob.id` (form `job-<hex>`) is the universal identity key end to end:
the `MAIKO_JOB_ID` env var, the session-registry key, `AgentMessage.task_id`, the
monitor bucket key, the tmux session suffix.

**AgentProfile** is the character sheet (`models/agent_profile.py`): `display_name`,
`avatar` (a card id), `flavor_text` (the self-written bio/tagline), `role`
(`coding`/`review`/`investigation`/`cartographer` or a custom-skill id),
`scope_repo`, `instructions` (per-profile markdown injected into every session, the
soul), live `state` (`idle`/`working`/`stuck`), and stats. **`(role, scope_repo)` is
the work-routing tuple.** No tiers, no seniority; a profile owns a slice of work.
Identities are generated lazily: `create_profile()` rolls a rarity-weighted
personality card from `data/cards/cards.yaml`, sits on an "Arriving…" placeholder,
and a background thread asks the LLM (Ollama by default, on-vibe weird/blunt prompt)
for a name + tagline + bio.

### Spawning

Three entry points (manual assign, the `spawn_jobs_for_tasks` phase, a skill run) all
converge on the same machinery, executed by the `execute_agent_jobs` phase (≤2 queued
jobs/cycle):

```
 AgentJob(queued) ─► resolve role + repo + model/effort/runtime
                  ─► maybe_spawn(role, scope_repo) → AgentProfile
                  ─► prepare():
                        worktree.py:  git worktree add -b <branch> <path> <base>
                                      (PR jobs fetch pull/<n>/head; repo-less
                                       roles get a scratch dir keyed by job id)
                        scaffold.py:  TASK.md, CLAUDE.md (role protocol +
                                      character + Repo Overview/Team Playbook +
                                      notes + specialty), .mcp.json (only the
                                      user's inherited project MCPs),
                                      .claude/settings.json (hooks), .maiko-env.json
                  ─► kickoff.py: daemon thread "agent-<job>"
                        claim per-job lock, session_id=uuid4, set state=working
                        runtime.spawn(... extra_env={"MAIKO_JOB_ID": job.id})  [BLOCKS]
                        on exit: state=idle, release lock; crash w/o reply → failed
                  ─► job.status=running, started_at, session_id; Task → in_progress
```

Worktrees live under `<repo>/.maiko-worktrees/<branch>`. The `-b` (always a fresh
branch) is deliberate so a stale branch's leftover `TASK.md`/`PLAN.md` cannot leak
into a new task. Cleanup is paranoid: it refuses to `rmtree` anything lacking the
`.maiko-worktrees` marker.

### The runtime abstraction

`agents/runtimes/base.py` defines `AgentRuntime`: a mandatory synchronous side
(`send` / `send_json`, used for all of Maiko's own LLM calls) and an optional async
side (`spawn` / `resume` / `end_session` / `session_transcript_path`, for driving a
long-running agent). Concrete runtimes:

- **`claude-code`** (default): `claude --print --output-format text --session-id`,
  prompt piped via stdin (avoids the Windows argv cap). Session context is the
  on-disk JSONL transcript at `~/.claude/projects/{escaped}/{sid}.jsonl`, which is
  authoritative for resume.
- **`claude-code-tmux`** (Mac-first): drives interactive `claude` inside a tmux pane.
  Exists for one reason: `--print` bills the Agent-SDK credit pool, the interactive
  TUI bills the larger subscription pool. `is_persistent_session() = True`.
- **`ollama`**: send-only, OpenAI-compatible. Routes Maiko's *internal* calls
  (overview, agent bios, scene notes) to a local model at zero cost. It does not
  implement spawn/resume, so agent kickoffs naturally fall back to a spawn-capable
  runtime.

Selection is `brain_session._get_runtime(task_type)`: a per-task-type routing rule
(`agents/routing.py`) can pick a runtime; if it is unavailable Maiko silently falls
back to the default, and never returns None. The same `task_type` key must be passed
to `resolve_model()` and `_get_runtime()` or per-task rules silently no-op. Model and
effort are routed the same way (`resolve_model` / `resolve_effort`, defaults: Haiku
for triage/classify, Sonnet for chat/skill, Opus for coding and the heavy generators).

### Monitoring

There is no subprocess poller. Completion is event-driven: the agent calls
`maiko reply --type ready_for_review`, the outbox flips the job to `done`. The only
watchdogs are `wake.check_stuck_agents` (15 min working + no activity → `stuck`
pupdate) and the kickoff thread (subprocess died without replying → `failed`).
`monitor.py` is purely read-side: it derives each agent's active/idle/stale/waiting
status from its recent pupdates and overlays the latest speech-bubble message.

---

## 7. A2A communication and the maiko CLI

There is no message bus and (anymore) no MCP server in the worktree. The entire
agent↔Maiko and agent↔agent channel is the `AgentMessage` table plus the `maiko` CLI
plus a couple of Claude Code hooks.

**AgentMessage** (`models/agent_message.py`): `task_id` (= the AgentJob id, the
routing key), `direction` (`to_agent`/`from_agent`), `sender`
(`brain`/`user`/`agent`/`maiko`), `recipient` (None = in-thread chatter only visible
if you open chat; `"user"` = materializes a Memo into the inbox), `content`,
`message_type` (`message`/`directive`/`context`/`stop` plus the terminal types
`ready_for_review`/`stuck`/`plan_for_approval`/`pr_opened` and
`insight`/`feedback`/`status`).

**Inbox / outbox** (`api/agents_api.py`, `api/agent_outbox.py`):

- An agent reads its inbox with `maiko inbox` → `GET /agents/<job>/inbox`. It does
  not need to remember to: the `Stop` hook polls the inbox and *blocks the agent's
  stop* if there are unread messages, feeding them back so the agent picks them up
  before settling. `PostToolUse` also polls every tool boundary.
- A user/brain message is `POST /agents/<job>/inbox`. If `sender="user"` it also
  calls `wake_agent(...)` so the message is actually read instead of waiting for the
  next trigger.
- The agent talks back with `maiko reply "<text>" --type <type>` →
  `POST /agents/<job>/outbox`. `handle_agent_job_reply` parses it:
  `ready_for_review` → parse `VERDICT:`/`SUMMARY:` + `PATTERN:`/`PROPOSAL:` blocks →
  `job.status=done`, Task → `review`, emit an `agent_ready` Memo, end the runtime
  session; `insight` → pending `Insight`; `feedback` → `Signal`; `recipient="user"`
  → an inbox Memo deep-linking `/jobs/<id>?view=chat`.

**The wake mechanism** (`agents/wake.py`) is the single entry point to *resume* a
session. It adds three things over a raw `runtime.resume()`: a per-job lock so two
triggers cannot race, a per-source policy (chat/feedback/review/plan messages queue
and wait their turn; nudge/heartbeat/status drop silently when busy), and
`AgentProfile.state` bookkeeping. It re-resolves the session from the registry and
falls back to the `AgentJob` row, which is what lets a resume survive a server
restart that purged the in-memory registry.

```
  user ─POST /agents/<job>/inbox─► AgentMessage(to_agent) ─(if user)─► wake_agent
  agent self-poll: Stop hook + PostToolUse ─GET inbox─► reads messages
  agent ─`maiko reply --type X`─► POST /agents/<job>/outbox ─► handle_agent_job_reply
        ready_for_review → job done, Task review, agent_ready Memo, end session
        insight → Insight   feedback → Signal   recipient=user → inbox Memo
  wake.py: per-job lock | queue {chat,feedback,review,plan} / drop {nudge,heartbeat}
           → runtime.resume(--resume <sid>)
```

**The maiko CLI** (`cli/`, entry point `planet_maiko.cli.main:main`) is both your
admin tool and the agent talk-back path. Three command families:

- **Agent-facing** (`agent_cmds.py`, thin HTTP clients, auto-resolve
  `MAIKO_JOB_ID` from env / `.maiko-env.json` / `TASK.md`): `report`, `reply`,
  `inbox`, `task`, `session-report`, `check-code` (mechanical verdict before
  `ready_for_review`), `leave-comment` (inline review comment), `feedback`, `sleep`,
  `wake`. `maiko task done` is deliberately refused; agents do not close tasks.
- **Admin** (`admin_cmds.py`, often build their own app context): `serve`, `status`,
  `backup` / `backup-list` / `restore`, `inspect-prompt`, `reset-skill`,
  `db-schema`.
- **LoRA** (`cli/lora_cmds/`): `train`, `retrain`, `eval`, `eval-prs`, `review`,
  `review-rag`, `rules-relevant` (the one agents call mid-task), `add-rule`,
  `dedup`, etc. Most run offline against the DB directly.

Because the protocol is shell commands rather than MCP tools, the same agent loop
works under any runtime, not just Claude.

---

## 8. Knowledge: signals to learnings

This is the system the README is proudest of: agents inherit your team's accumulated
taste without anyone hand-writing a guidelines doc.

Two models:

- **Signal** (`models/signal.py`): one raw feedback event. `category`, `text`
  (mutated in place by synthesis), `source_type` (`pr_comment` / `manual` /
  `agent_discovery` / `session_feedback` / `lora_hook` / `lora_correction`),
  `severity`, `repo`/`language`/`file_path`, `code_context`, `examples` (one signal,
  N training pairs), `external_id` (re-scrape dedup), `original_text` (the raw
  reviewer comment, preserved before synthesis rewrites `text`), `synthesized` (gate
  into clustering), `aggregated` (consumed by clustering), `learning_id`.
- **Learning** (`models/learning.py`): the deduplicated, generalized rule backed by N
  signals. `rule`, `category`, `scope_repo`, `is_global` (flipped when the rule
  appears across ≥3 repos), `confidence` (UI weight only, never gates graduation),
  `signal_count`, `status` (`incubating` → `pending` → `active` / `dismissed`),
  `aggregation_key`, and the RAG fields `violation_description` +
  `violation_embedding`.

A Signal is one observation. A Learning is the equivalence class of many Signals that
say the same thing, with one canonical rule text.

**PR backfill.** The GitHub poller's `_after_sync` scrapes inline review comments
from the last few **merged** PRs into `synthesized=False` Signals, using a
three-phase read-then-network-then-write batch so it never holds the SQLite write
lock across a slow API call. Dedup is triple-keyed (`external_id`, then
`(file_path, diff_hunk)`, then `(file_path, body[:120])`), with the `pr_merged`
pupdate's `extra.comments_scraped_at` as the per-PR cursor. A one-shot bulk
`bootstrap_from_prs` (the Knowledge page "Backfill" button) does the same across all
PRs in a repo.

**Synthesis** (`synthesizer.py`, brain phase 6). Only `pr_comment` +
`synthesized=False` signals. Batched to Haiku with the framing: synthesize into clean
rules *for an autonomous coding agent*. Anything that needs a human ("confirm with",
"check with", greetings, praise, bot noise, PR logistics, references to specific
people) is marked not-actionable and **deleted**. Junk filtering is upstream of
clustering, so by the time clustering runs every signal is rule-shaped.

**Clustering** (`clustering.py`, brain phase 7). The only path from Signal to
Learning. Per category, an LLM places each new signal into exactly one of: attach to
an existing Learning, **drop** (it matches a previously *dismissed* Learning, which
is how "dismiss sticks"), or start a new Learning. A safety net makes any unplaced
signal its own new Learning so nothing is lost. New Learnings start `incubating`; the
second corroborating signal promotes to `pending`. `pending → active` is **always**
an explicit human approval; there is no auto-graduation. A separate drift-dedupe pass
(`cluster_learnings`) merges duplicate Learnings that two cycles minted independently,
re-pointing signals to the keeper.

```
  PR merged ─► _after_sync scrape ─► Signal(synthesized=False)
  agent reply / hook / manual ─────► Signal(synthesized=True)
                                          │
                            [6 synthesis]  not-actionable → DELETE
                                          │ actionable: original_text←text,
                                          │ text←clean rule, synthesized=True
                                          ▼
                            [7 clustering]  LLM: attach | drop(=dismissed) | new
                                          ▼
                  Learning  incubating ──(2nd signal)──► pending ──(you approve)──► active
                            (loser of a drift merge or LoRA negative → dismissed)
                                          │
                              active rule ─► RAG embedding + coding-guidelines.md
                                             + (parked) LoRA training data
```

**Agents confess their own mistakes** through the same pipeline, two ways: inline
`PATTERN:` blocks in any one-shot run (parsed by `agent_output.py` into
`synthesized=True` signals), and the end-of-day campfire (§12) where each agent
replies with a `feedback` message that becomes a `session_feedback` Signal tied to
the exact `AgentMessage` so it can be cleanly undone.

Surfaces: the Knowledge page (`learning_api.py`) with approve/dismiss, the provenance
pane showing the real reviewer comment (`original_text`), and `data/coding-guidelines.md`.

---

## 9. RAG retrieval

"Describe what you're doing, get only the rules that matter" instead of stuffing 300
rules into every prompt.

**Embeddings** (`brain/learning/embeddings.py`): the local free
`sentence-transformers` model `BAAI/bge-small-en-v1.5` (loaded once, cached
to `~/.cache/huggingface`; falls back to a cache-only load if the hub
update/etag check fails). Embedding is **opt-in**: RAG stays dark until
`pip install -e ".[rag]"` provides the model, which is why the
violation-description backfill is not run at boot by default.

**What is embedded is not the violation.** `intent_extraction.py` generates a
*scenario description* for each active Learning: "the kind of change that should pull
this rule into a reviewer's attention", in active voice, deliberately matched in
grammatical voice to the diff-side descriptions for tighter cosine scores. It is
stored as a JSON float array in the `Learning.violation_embedding` column. There is
no vector extension; retrieval is a full scan of active embedded rules plus a
hand-rolled Python cosine. That is fine at hundreds of rules.

**Retrieval** (`rule_retrieval.py`): a rule's score is the **max** cosine across all
query descriptions (max, not avg or sum, so a single strong hit is not diluted).
Two entry strategies: `score_rules_for_diff` runs the diff through one Haiku call
that decomposes it into intent (1-3 entries) + operations (3-15 entries) at two
granularities, then embeds those; `score_rules_for_queries` takes an agent's own
free-text descriptions with **no LLM step** (cheaper and sharper, because an agent in
a worktree already has full context). This is the `maiko rules-relevant --query`
path. Top-K default 5, min similarity ~0.40-0.45 depending on the caller.

`rag_review.py` is the end-to-end review: retrieve top-K rules, feed Claude
(Sonnet) the diff plus each rule plus its scenario description, get
VIOLATION/OK/SKIPPED per rule, and any flag-worthy finding no rule covers comes back
as a `PATTERN:` block → a new pending Signal. The loop closes: review findings feed
the knowledge base that powers the next review.

```
  INDEX TIME (opt-in, background):
    active Learning ─► generate scenario description (Haiku) ─► embed (bge-small)
                    ─► Learning.violation_embedding (JSON column)

  QUERY TIME:
    diff ──(Haiku decompose)──┐        agent queries ──(no LLM)──┐
                              ▼                                  ▼
                         embed_batch ─► full scan active rules, repo-scoped
                         score = MAX cosine ─► ≥ min_sim ─► top-K
                              │
                              ├─► /rules/relevant  (just the rules)
                              └─► rag_review: Sonnet judges each → review
                                  + uncovered findings → PATTERN → new Signal
```

---

## 10. LoRA self-training (parked)

The experimental piece: train a small local LoRA adapter on your code/PR history so
your house style is enforced by a model that has actually seen your codebase. Honest
status: **the training and inference blueprints (`training_bp`, `lora_bp`) are
deliberately not registered in `app.py`** (the "lora-park"). The code stays in-repo,
dormant, reachable only via CLI. Treat this section as design intent, not a live path.

How it is built when run from the CLI: `trainer.py` shells `python -m mlx_lm.lora`
(Apple Silicon / MLX only; the PyTorch path is an explicit stub). Default base model
`mlx-community/Meta-Llama-3.1-8B-Instruct-4bit`. Training pairs are
`{input, "VIOLATION: [category] <rule>" | "PASS"}`, drawn from real merged-PR
feedback, unincorporated Signals (labeled with the canonical `Learning.rule` so it
becomes ~300-way classification rather than open generation), Opus-generated
synthetic violations/passes per active rule, and a `corrections.jsonl` of
false-positive/false-negative reports. The split is a deterministic seeded 80/20;
corrections are up-weighted into the **train** set only (duplicating into holdout
would inflate F1). Custom early stopping watches validation-loss plateau.

Two evaluations: pair-level precision/recall/F1 persisted as `AdapterEval` rows, and
a more honest PR-level holdout harness (`eval/holdout.py`) that scores "did the model
flag the same files a human reviewer did", fetching the *point-in-time* diff the
reviewer actually saw and caching ground truth so a later comment edit does not shift
recall between training cycles. Optional LLM-as-judge mode for semantic recall.

RAG and LoRA share the same `Signal` → `Learning` substrate: a graduated
false-negative becomes a rule that gets both a RAG embedding and synthetic LoRA
pairs.

---

## 11. Insights and the playbook

Easy to confuse with Learnings, so be precise:

- **Learning** = a trainable/retrievable *code rule*. Confidence-gated, RAG-embedded,
  feeds (parked) LoRA. Human-approved.
- **Insight** (`models/insight.py`) = *tribal/operational knowledge* injected
  **verbatim** into agents. "Use IntelliJ for tests, the CLI runner is broken." Not
  embedded, not trained, not confidence-gated. `repo_scope` (null = global),
  `status` (`pending`/`active`/`dismissed`), `expires_at` (TTL for in-flight notes
  like a migration in progress), `source_message_id`.

`brain/learning/playbook.py:build_playbook()` selects active in-scope Insights and
renders two CLAUDE.md blocks at worktree prep time: `## Repo Overview` (Insights
tagged `overview`, the cartographer's cold-start architecture map) and
`## Team Playbook` (everything else, as a bullet list). This is how every new agent
reads the lay of the land before doing anything. Insights are produced by the
cartographer role and by agents replying `--type insight` (notably during the
campfire).

---

## 12. Pack Insights: the campfire ritual

The signature feature, and on-vibe by design: at end of day the whole pack gathers
"around the campfire" (rendered as a moon, not a literal fire, Earthbound-surreal)
and each live agent reflects on its workday.

It is **manually triggered only**: no cron, no automation. State
(`brain/learning/pack_insights.py`) is an in-memory machine
(`idle → gathering → reviewing → synthesized → finalized → idle`), not a table; a
summary pupdate is written for visibility but the ritual does not survive a restart.

`start_gathering()` computes the alive pack (running jobs plus followup-kind jobs with
a worktree on disk), drops a campfire prompt into each agent's inbox, and wakes them.
Each agent is asked to reply with up to three things: `feedback` (a one-sentence
coding rule it learned → a `session_feedback` Signal → the knowledge pipeline),
`insight` (operational note → a pending Insight → the playbook), and `summary` (one
cheerful line for its speech bubble at the fire). The durable output is written
**at reply time**, not at finalize, and tied to the originating `AgentMessage.id`.

The UI (`AgentsInsightsTab.jsx`) renders the pack in a ring with speech bubbles,
polling every few seconds. You review per-agent with Keep/Drop (default keep), and
"Wrap up" applies only the drops (hard-deletes dropped Signals, soft-dismisses
dropped Insights) and resets to idle. `finalize_pack_insights` is in the
`NEEDS_CONFIRMATION` guardrail tier; nothing reaches agent context without your nod.

---

## 13. Memos: the durable inbox

The newer canonical surface for "you need to see or decide something", explicitly
distinct from Pupdate (transient, auto-dismissed once routed) and Task (concrete work
with a worktree). Memos are the middle ground, and they are what replaced
pupdate-driven inbox/home rendering.

**Model** (`models/memo.py`): `kind` (open set: `skill_result`, `notification`,
`agent_ready`, `agent_stuck`, `agent_proposal`, `agent_plan`, `job_approval`,
`agent_message`), `category` (`info`/`waiting`/`offer`), `title`, `body`, `url`,
`cta_label` + `cta_action` (a short token the UI maps to approve/review/open),
`priority`, provenance (`source_agent_id`/`source_task_id`/`source_pupdate_id`),
`status` (`pending` → `seen` → `actioned`/`dismissed`), `extra` (kind-specific
payload: `job_spec` for job_approval, `draft` for proposals, full output for
skill_result).

All producers go through `create_memo()`, which validates category and dedupes on
`source_pupdate_id` so a re-firing automation cannot stack duplicates. Approve
handlers are registered per kind (`brain/memo_handlers.py`): `agent_proposal` mints a
routed Task from the draft; `job_approval` mints the real AgentJob from `job_spec`
and raises a 422 `needs_repo` when a worktree-requiring kind has no local clone, so
the frontend can prompt and retry. Only `pending`/`seen` memos feed the overview LLM;
`actioned`/`dismissed` are archive. Surfaces: `MemosPane.jsx` (the unified Home
feed), the overview `needs[]` cards, and `/home/review-queue` (the exhaustive
companion to the curated overview).

---

## 14. Awareness: conflict detection and expertise

**Conflict detection** (`brain/awareness/conflicts/`, brain phase 3) catches sibling
agents about to collide. Over Maiko-prepared worktrees only (≥2), it snapshots each
worktree's diff (committed `origin/main...HEAD` plus staged; unstaged is excluded as
noise; Maiko's own scaffolding files are filtered out so every pair does not
false-positive on `TASK.md`/`CLAUDE.md`), builds a file→agents index, and returns
direct pairwise overlap edges (no transitive clustering). Severity: `hard` for a
config file or an overlapping method name, `soft` for the same file different
methods. Method extraction uses tree-sitter for Java, the `ast` module for Python, a
diff-hunk heuristic otherwise.

Acting on it: `send_conflict_warnings` posts one warning message per agent per
conflict (first time only, deduped by a deterministic-id pupdate). The expensive
`resolve_conflicts` path asks both agents what they are changing, has each classify
the overlap as compatible/duplicate/conflict, and either lets them continue, pauses
one, or escalates a high-priority actionable pupdate. A six-hour grace window after
you dismiss an escalation stops the 5-minute cycle from nagging.

**Expertise** (`brain/awareness/expertise.py`) is a separate, non-cycle concern: a
time-decayed graph (`1/(1+days_ago/30)`) built from merged-PR authorship, plus
reviewer focus profiles aggregated from PR-comment Signals. It feeds review routing.
API only, no dedicated page.

---

## 15. The home overview and the scene

**The overview** (`brain/overview.py`) is the rolling LLM-generated narrative that is
the primary surface of the app. It is on-demand and cache-backed (a file,
`data/overview.json`, 4h max age), never blocking: stale cache returns immediately
while a background daemon thread regenerates.

Regeneration is the "live wake" architecture and is the most expensive single thing
Maiko does deliberately. It force-refreshes every poller in parallel, runs one full
brain cycle so context is fresh, then **wakes every running agent** with a "you just
walked into the town square, coffee-machine moment" prompt and waits up to ~20s for
each to reply with a status line, so the narrative can weave in live agent voices.
Then it runs the `home-overview` skill as a full Claude Code agent (so it can look
things up, not just summarize) over a context bundle of memos, tasks, calendar,
agents, pollers, the scene, a closing-window/weekend/interruption-budget signal, and
your custom prompt. Output is a defensively-defaulted JSON with `greeting`,
`summary`, `focus[]`, `needs[]`, `alive`, `closing`, `sprite`, `overnight[]`.

**The scene** (`brain/creativity/scene.py`) is a pure rules engine, no LLM. It
combines time-of-day, season (hemisphere-aware via latitude), an anchored 29.53-day
moon phase, holidays, and live weather (Open-Meteo, free, no key, 1h cache) into a
pixel-art descriptor: sky, hills, celestial body, weather overlay, decorative
specials, and Maiko's outfit (a witch hat in October, asleep at night). It renders as
the app-chrome background and drives the Today widget's moon/weather line.

---

## 16. Projects driver and guardrails

**Projects** (`brain/projects/driver.py`, brain phase 10): a `Project` carries a JSON
list of phases and a `current_phase` index. Each cycle the driver advances: if the
current phase's tasks are all done it marks the phase done; next cycle it activates
the next phase, auto-creates that phase's task, and notifies via pupdates; when the
index passes the end the project is done. This is the legacy linear single-track
advancer. DAG / multi-step workflow templates are deferred post-launch by design;
`when→then` automations plus this driver are the MVP.

**Guardrails** (`brain/guardrails.py`) is a static permission table, not a runtime
enforcer: `AUTONOMOUS` (mark_read, create_task, dismiss_low_priority),
`SEMI_AUTONOMOUS` (create/advance project, prepare_agent, run_skill,
send_agent_message), `NEEDS_CONFIRMATION` (approve_plan, merge_pr, push_code,
delete_task, stop_agent, finalize_pack_insights). Callers consult it to decide
whether to act, log-and-act, or queue for your review.

---

## 17. Plugin architecture

Extend Maiko without forking it. `plugins/__init__.py` exports `MaikoPlugin`,
`get_plugins()`, `fire_hook()`.

**The `MaikoPlugin` contract** (`plugins/base.py`, every hook optional):

| Hook | When it fires |
|---|---|
| `on_startup(app)` | Once at `create_app()`, only if enabled. Register blueprints/tables. |
| `on_cycle_tick(app)` | Once per brain cycle. Periodic work (this is where pollers poll). |
| `on_brain_cycle(phase, results, app)` | Once per cycle phase. |
| `on_pupdate_created(pupdate)` / `on_task_created(task)` | On row creation. |
| `register_commands(subparsers)` | Add `maiko` CLI subcommands. |
| `get_config_defaults()` | Dict merged into config under the plugin's key. |
| `get_config_schema()` | Renders the Settings form (auto-inferred from defaults if omitted; secret-like keys auto-masked). |
| `register_pupdate_types()` / `register_actions()` / `action_handlers()` | Extend the Automation editor's when/then. A plugin cannot shadow a core action kind. |
| `get_setup_actions()` / `run_setup_action(key)` | User-triggered buttons in Settings (e.g. Linear "import"). |
| `register_default_automations()` | Seed starter automations (`created_by="plugin:<name>"`). |
| `emit_pupdates(...)` | Concrete helper: the dedup+insert gate (not overridden). |

**Discovery** (`plugins/loader.py`), three sources, precedence built-in <
entry-point < local: `plugins/builtin/*.py`; the `planet_maiko.plugins` entry-point
group (pip packages); and `~/.maiko/plugins/*.py`. Dedup by `name`, first wins, so a
local file can override a builtin. Disabled plugins (in `config.plugins.disabled`)
are never instantiated; toggling requires a restart.

**`PollerPlugin`** (`plugins/poller.py`) is the scheduled-fetch shape: an in-memory
interval throttle in `on_cycle_tick`, then the pipeline `poll(config)` →
`to_pupdates(raw)` → `to_signals(raw)` → `emit_pupdates(...)` → `_after_sync(...)`,
each stage exception-isolated. Subclasses implement `poll` and `to_pupdates`; they
get dedup, signals, the Settings form, and the Automation dropdowns for free. The
four builtins (`github`, `linear`, `calendar`, `pagerduty`) are all `PollerPlugin`
subclasses; the shared API clients live in `plugins/clients/` and verify TLS via
`certifi`.

---

## 18. Frontend and desktop shell

React 19 + Vite 8 + react-router 7, packaged by Tauri 2. `frontend/src/App.jsx`:
`BrowserRouter` with eager Home/Tasks/Agents and lazy Settings/Knowledge/Automations/
Themes/AgentJob chunks, plus four always-mounted globals (toasts, AskMaiko, an
arrival watcher, the PersistentPack dock). The nav is five persistent pills in a
frosted top bar (despite the file being named `Sidebar.jsx`): Home, Tasks, Pack
(`/agents`), Knowledge (`/knowledge`), Automations; Settings via a gear icon. Old
routes redirect so bookmarks survive.

The API client (`frontend/src/api/client.js`) is one flat object of ~150 wrappers
over the Flask API on `:8420`, with a 5s in-memory GET cache that any mutation
flushes, and structured-error propagation (`err.status`/`err.body`) so callers can
act on a 422 `needs_input`. It uses a relative `/api` when Flask serves the bundled
SPA and the absolute origin in dev/Tauri (CORS is blanket-enabled).

The Tauri shell (`frontend/src-tauri/src/main.rs`) exists for one job: one icon
launches both. It spawns `maiko serve` as a child (via a login shell on macOS/Linux
so the Flask child inherits the full PATH for `gh`, the venv, and
`ANTHROPIC_API_KEY`), drains its logs to a file, and kills it on window close or
quit. `tauri-plugin-single-instance` prevents double launches. If `maiko` is missing
it opens the window anyway, because a visible "API down" beats a silent pre-window
crash. The frontend build output is written into `src/planet_maiko/static/`, the same
files Flask serves.

---

## 19. Sharp edges and parked work

Honest notes for future-you, since you asked for "anything else":

- **The LoRA verifier is parked.** `training_bp`/`lora_bp` are intentionally not
  registered; the train/eval/inference pipelines are dormant in-repo and only
  reachable via CLI. There is also a latent `NameError` on the MLX inference path
  (`inference.py` references `DEFAULT_TRAINING_CONFIG` without importing it), which
  the lora-park masks because the routes do not exist. If you revive the verifier,
  fix that import first.
- **Embedding-model-mismatch is documented but not enforced.** The embedding model
  name is supposed to be stored alongside each vector and never compared across
  models; nothing actually persists it. Different-dimension models are saved by the
  length guard in `cosine_similarity`, but two same-dimension different models would
  silently produce garbage scores. Relevant only if you switch embedding backends
  with rules already indexed.
- **Pupdates are mid-migration to Memos.** The agent-gated kinds
  (`ready_for_review`/`stuck`/`plan_for_approval`) are now Memos; their entries still
  exist in `ACTION_TYPES`/`pupdate_types.py`. When in doubt, the durable user-facing
  surface is a Memo, the routing currency is a Pupdate.
- **No tests, on purpose.** Per the project's own posture, Maiko is in heavy
  architectural churn and not yet in real use; tests come once the shape stabilizes.
- A few stale strings noted by exploration (an LGPL header in `__init__.py` vs the
  authoritative AGPL in `pyproject.toml`; a docstring saying "eight" seed rules where
  there are eleven; docstrings pointing at a non-existent `brain/automations/engine.py`).
  Cosmetic, listed so they do not surprise you later.

---

## 20. Where to look

| You want to change... | Start here |
|---|---|
| The cycle / phase order | `brain/cycle.py`, `brain/phases/` |
| What an external event becomes | the relevant `plugins/builtin/*.py`, `plugins/base.py:emit_pupdates` |
| Routing an event to work | `brain/automations/` (`conditions.py`, `actions/`), `automation_actions.py` |
| How an agent spawns | `agents/runtime/` (`worktree`, `scaffold`, `kickoff`, `process`) |
| Agent runtime backends | `agents/runtimes/` (`base`, `claude_code`, `tmux_claude`, `ollama`) |
| Agent ↔ Maiko protocol | `cli/agent_cmds.py`, `api/agent_outbox.py`, `agents/wake.py`, `hooks/` |
| Work routing (role, repo) | `orchestration.py`, `agents/routing.py` |
| The knowledge pipeline | `brain/learning/` (`synthesizer`, `clustering`, `playbook`), `models/{signal,learning}.py` |
| RAG retrieval | `brain/learning/{embeddings,intent_extraction,rule_retrieval,rag_review}.py` |
| The campfire | `brain/learning/pack_insights.py`, `api/pack_insights_api.py` |
| The inbox surface | `brain/memos.py`, `brain/memo_handlers.py`, `models/memo.py` |
| The home narrative | `brain/overview.py`, `api/home_api.py` |
| The ambient world | `brain/creativity/scene.py`, `api/scene_api.py` |
| App wiring / migrations | `app.py`, `database.py`, `config.py`, `paths.py` |
| Adding an integration | subclass `PollerPlugin` (`plugins/poller.py`); see `GUIDE.md` |

For the conceptual model and the plugin/runtime extension tutorials, see
[`GUIDE.md`](GUIDE.md). For the feature list, [`FEATURES.md`](FEATURES.md).
