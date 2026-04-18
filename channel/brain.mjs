#!/usr/bin/env node
/**
 * Maiko Brain — MCP server for external orchestrators to query Planet Maiko.
 *
 * Sibling to index.mjs. While index.mjs serves LLM agents Maiko spawns inside
 * its own worktrees (task-scoped, push-notification channel), this server
 * serves external tools running their own LLM sessions so they can register
 * those sessions with Maiko for agent-to-agent conflict detection.
 *
 * Phases A–D — the full v0 consumer surface:
 *   - A2A conflict detection: register / complete / detect
 *   - Read surface: learnings, conventions (Pack Insights), adapter info
 *   - Compliance: run the repo's LoRA against a diff
 *   - Producer hooks: submit feedback (signals / LoRA corrections) and
 *     submit insights (tribal knowledge for future agents)
 *
 * Environment variables:
 *   MAIKO_API_URL   — Planet Maiko API base URL (default: http://localhost:8420/api)
 *   MAIKO_CONSUMER  — optional label identifying the caller (audit/telemetry
 *                     only, no auth). Forwarded on session-creating calls.
 *
 * Usage in .mcp.json:
 *   {
 *     "mcpServers": {
 *       "maiko-brain": {
 *         "command": "node",
 *         "args": ["./channel/brain.mjs"],
 *         "env": { "MAIKO_CONSUMER": "my-tool-name" }
 *       }
 *     }
 *   }
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

const API_URL = process.env.MAIKO_API_URL || "http://localhost:8420/api";
const CONSUMER = process.env.MAIKO_CONSUMER || null;

const mcp = new Server(
  { name: "maiko-brain", version: "0.2.0" },
  {
    capabilities: {
      tools: {},
    },
    instructions: [
      `You have nine tools for coordinating an external LLM session with Planet Maiko:`,
      `- maiko_register_session(repo, worktree_path, session_id?, hint?) — announce`,
      `  that a new session is starting work in a given repo/worktree. Returns`,
      `  the session_id Maiko will use to track it for conflict detection.`,
      `- maiko_detect_conflicts(session_id) — list any active conflicts between`,
      `  this session and other registered sessions (overlapping files, etc).`,
      `  Safe to call repeatedly; pure query, no side effects.`,
      `- maiko_complete_session(session_id, outcome?) — mark the session finished`,
      `  so Maiko can stop tracking it for conflicts.`,
      `- maiko_get_learnings(repo, language?) — fetch the active learnings brief`,
      `  for a repo (prose for LLM context + structured list). Cache per session.`,
      `- maiko_get_conventions(repo) — fetch the Repo Overview + Team Playbook`,
      `  block to prepend to your agent's system prompt. Cache per session.`,
      `- maiko_get_adapter_info(repo) — fetch LoRA adapter metadata for a repo`,
      `  to decide whether compliance checks are worth it. Cache per session.`,
      `- maiko_check_compliance(diff, repo, scope?) — run the repo's LoRA against`,
      `  a diff you provide; returns violations. Slow (runs inference server-side);`,
      `  call as a pre-commit gate, not per edit.`,
      `- maiko_submit_feedback(type, content, context?) — feed corrective signal`,
      `  back to Maiko's learning pipeline. type is "signal", "false_positive",`,
      `  or "false_negative".`,
      `- maiko_submit_insight(repo, text, tags?, session_id?) — submit a tribal-`,
      `  knowledge insight about the repo. Lands as "pending" for user approval.`,
      ``,
      `Call maiko_register_session once at the start of a task,`,
      `maiko_detect_conflicts before and during edits to catch overlap with`,
      `other agents, and maiko_complete_session once the task ends. Fetch the`,
      `read-surface tools (learnings / conventions / adapter info) once at`,
      `session start and reuse the results — they only change at brain-cycle`,
      `cadence or on user approvals. Call maiko_check_compliance as a`,
      `pre-commit gate when a LoRA is configured for the repo. Use the`,
      `submit_* tools sparingly — when you've observed something worth`,
      `feeding back (a correction, a team convention, a gotcha a future`,
      `agent should know).`,
    ].join("\n"),
  }
);

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "maiko_register_session",
      description:
        "Register a new external LLM session with Planet Maiko so it can " +
        "be tracked for agent-to-agent conflict detection. Call this once " +
        "at the start of a task, before any edits. Returns the session_id " +
        "Maiko assigned (either the one you supplied or a freshly-generated uuid).",
      inputSchema: {
        type: "object",
        properties: {
          repo: {
            type: "string",
            description: "Repository identifier in \"org/name\" form (e.g. \"acme/widgets\").",
          },
          worktree_path: {
            type: "string",
            description: "Absolute path to the working directory / worktree this session will edit.",
          },
          session_id: {
            type: "string",
            description:
              "Optional consumer-supplied session id. If omitted, Maiko " +
              "generates a uuid4 hex and returns it in the response.",
          },
          hint: {
            type: "string",
            description: "Optional short task description (e.g. \"refactor auth middleware\").",
          },
        },
        required: ["repo", "worktree_path"],
      },
    },
    {
      name: "maiko_complete_session",
      description:
        "Mark an external session as finished. After this, Maiko stops " +
        "tracking the session for conflict detection. Call once the task " +
        "is done or abandoned. Idempotent — calling on an already-completed " +
        "session is a no-op.",
      inputSchema: {
        type: "object",
        properties: {
          session_id: {
            type: "string",
            description: "The session_id returned by maiko_register_session.",
          },
          outcome: {
            type: "object",
            description:
              "Optional structured outcome payload (e.g. {\"status\": \"merged\", \"pr_url\": \"...\"}). " +
              "Free-form; Maiko stores it alongside the session for later inspection.",
          },
        },
        required: ["session_id"],
      },
    },
    {
      name: "maiko_detect_conflicts",
      description:
        "Query Maiko for any active conflicts between this session and " +
        "other registered sessions (e.g. overlapping files being edited " +
        "concurrently). Returns a list of conflicts with severity, file, " +
        "and the conflicting session's id. Safe to call repeatedly — pure " +
        "query, does not emit notifications or create pupdates (the brain " +
        "cycle handles emission separately at its own cadence).",
      inputSchema: {
        type: "object",
        properties: {
          session_id: {
            type: "string",
            description: "The session_id returned by maiko_register_session.",
          },
        },
        required: ["session_id"],
      },
    },
    {
      name: "maiko_get_learnings",
      description:
        "Fetch the active learnings for a repo — both the prose brief " +
        "(designed to drop into LLM context) and the structured list behind " +
        "it. Optionally filter by language (e.g. \"python\"). " +
        "Cache this result for the duration of your session — data only " +
        "changes at brain-cycle cadence (~5 min), so don't re-fetch before " +
        "each individual edit. Fetch once at session start and reuse.",
      inputSchema: {
        type: "object",
        properties: {
          repo: {
            type: "string",
            description: "Repository identifier in \"org/name\" form (e.g. \"acme/widgets\").",
          },
          language: {
            type: "string",
            description:
              "Optional language filter (e.g. \"python\", \"typescript\"). " +
              "When set, only learnings scoped to that language are returned.",
          },
        },
        required: ["repo"],
      },
    },
    {
      name: "maiko_get_conventions",
      description:
        "Fetch the \"Repo Overview + Team Playbook\" tribal-knowledge block " +
        "Maiko would inject into a new agent's CLAUDE.md for this repo. " +
        "External orchestrators should prepend this to their own agent's " +
        "system prompt so the agent works with the same conventions Maiko's " +
        "own agents get. " +
        "Cache this result for the duration of your session — the playbook " +
        "only changes when users approve new insights. Fetch once at session " +
        "start, re-use throughout.",
      inputSchema: {
        type: "object",
        properties: {
          repo: {
            type: "string",
            description: "Repository identifier in \"org/name\" form (e.g. \"acme/widgets\").",
          },
        },
        required: ["repo"],
      },
    },
    {
      name: "maiko_get_adapter_info",
      description:
        "Fetch metadata about the LoRA adapter trained for this repo (if " +
        "any). Use this to decide whether it's worth calling compliance " +
        "checks on this session's edits later — if no adapter exists, the " +
        "repo has no trained model to compare against yet. " +
        "Cache this result for the session — adapter metadata only changes " +
        "when a training run completes.",
      inputSchema: {
        type: "object",
        properties: {
          repo: {
            type: "string",
            description: "Repository identifier in \"org/name\" form (e.g. \"acme/widgets\").",
          },
        },
        required: ["repo"],
      },
    },
    {
      name: "maiko_check_compliance",
      description:
        "Run the repo's LoRA compliance check against a diff you provide. " +
        "Returns violations the model flagged (category, severity, message). " +
        "Triggers LoRA inference server-side and can take a few seconds — " +
        "call as a pre-commit gate, not per edit. If no adapter is trained " +
        "for the repo, returns an empty list with no_model_for_repo=true " +
        "so the caller can skip gracefully (check maiko_get_adapter_info " +
        "first to avoid the round-trip).",
      inputSchema: {
        type: "object",
        properties: {
          diff: {
            type: "string",
            description:
              "Git diff text (output of `git diff <base>..HEAD` or similar).",
          },
          repo: {
            type: "string",
            description: "Repository identifier in \"org/name\" form.",
          },
          scope: {
            type: "string",
            enum: ["branch", "last_commit"],
            description:
              "Informational only — recorded with the check for audit. " +
              "Doesn't change processing (the caller provides the diff).",
          },
        },
        required: ["diff", "repo"],
      },
    },
    {
      name: "maiko_submit_feedback",
      description:
        "Contribute corrective feedback back to Maiko's learning pipeline. " +
        "Three flavors via `type`: \"signal\" for a generic observation " +
        "(team convention, noticed pattern), \"false_positive\" when " +
        "Maiko's LoRA flagged something that's actually fine, and " +
        "\"false_negative\" when the LoRA missed a real issue. Submissions " +
        "feed the next retraining cycle. Use sparingly — one per clear " +
        "observation, not per edit.",
      inputSchema: {
        type: "object",
        properties: {
          type: {
            type: "string",
            enum: ["signal", "false_positive", "false_negative"],
            description:
              "\"signal\" for a generic observation feeding the learning " +
              "pipeline. \"false_positive\" when the LoRA flagged code " +
              "that's actually correct. \"false_negative\" when the LoRA " +
              "missed a real issue.",
          },
          content: {
            type: "string",
            description:
              "The primary payload. For \"signal\", the text of the " +
              "observation. For \"false_positive\" / \"false_negative\", " +
              "the code snippet the LoRA judged incorrectly.",
          },
          context: {
            type: "object",
            description:
              "Extra fields. \"signal\" requires { category }. " +
              "\"false_negative\" requires { violation }. All types " +
              "accept optional { repo, file, code, category, reason, " +
              "session_id, language, severity }.",
          },
        },
        required: ["type", "content"],
      },
    },
    {
      name: "maiko_submit_insight",
      description:
        "Submit a tribal-knowledge insight about the repo (tooling quirks, " +
        "migration state, team conventions, gotchas). Insights get reviewed " +
        "by the user and, if approved, injected into every future Maiko " +
        "agent's CLAUDE.md for the same repo. Submitted as \"pending\" by " +
        "default — the user approves via the Playbook UI. Use sparingly: " +
        "one fact per call, only for durable knowledge a future agent " +
        "working in this repo would benefit from.",
      inputSchema: {
        type: "object",
        properties: {
          repo: {
            type: "string",
            description: "Repository identifier in \"org/name\" form.",
          },
          text: {
            type: "string",
            description:
              "The insight — one fact, one or two sentences (e.g. " +
              "\"this repo uses IntelliJ for tests; the CLI runner is broken\").",
          },
          tags: {
            type: "array",
            items: { type: "string" },
            description:
              "Optional tags (e.g. [\"tooling\"], [\"migration\"]). The " +
              "\"overview\" tag is reserved for Cartographer-authored " +
              "repo maps and shouldn't be used by external submissions.",
          },
          session_id: {
            type: "string",
            description:
              "Optional — the session_id returned by maiko_register_session. " +
              "When provided, the insight is attributed to this external " +
              "session in the Playbook UI.",
          },
        },
        required: ["repo", "text"],
      },
    },
  ],
}));

mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name === "maiko_register_session") {
    const { repo, worktree_path, session_id, hint } = req.params.arguments || {};
    const body = { repo, worktree_path };
    if (session_id) body.session_id = session_id;
    if (hint) body.hint = hint;
    if (CONSUMER) body.consumer = CONSUMER;

    try {
      const resp = await fetch(`${API_URL}/sessions/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to register session: ${err}` }] };
      }
      const data = await resp.json();
      const registered = data.registered_at ? ` at ${data.registered_at}` : "";
      return {
        content: [{
          type: "text",
          text: `Registered session ${data.session_id}${registered}.`,
        }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "maiko_complete_session") {
    const { session_id, outcome } = req.params.arguments || {};
    const body = {};
    if (outcome !== undefined) body.outcome = outcome;

    try {
      const resp = await fetch(`${API_URL}/sessions/${encodeURIComponent(session_id)}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to complete session: ${err}` }] };
      }
      const data = await resp.json();
      const completed = data.completed_at ? ` at ${data.completed_at}` : "";
      const status = data.status ? ` (${data.status})` : "";
      return {
        content: [{
          type: "text",
          text: `Completed session ${data.session_id}${status}${completed}.`,
        }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "maiko_detect_conflicts") {
    const { session_id } = req.params.arguments || {};
    try {
      const resp = await fetch(
        `${API_URL}/sessions/${encodeURIComponent(session_id)}/conflicts`
      );
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to fetch conflicts: ${err}` }] };
      }
      const data = await resp.json();
      const conflicts = data.conflicts || [];
      if (conflicts.length === 0) {
        return {
          content: [{
            type: "text",
            text: `No active conflicts for session ${data.session_id || session_id}.`,
          }],
        };
      }
      const formatted = conflicts
        .map((c, i) => {
          const severity = c.severity || "unknown";
          const file = c.file || "(no file)";
          const other = c.other_session_id || c.session_id || "(unknown session)";
          return `${i + 1}. [${severity}] ${file} — conflicts with session ${other}`;
        })
        .join("\n");
      return {
        content: [{
          type: "text",
          text: `Found ${conflicts.length} active conflict(s) for session ${data.session_id || session_id}:\n${formatted}`,
        }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "maiko_get_learnings") {
    const { repo, language } = req.params.arguments || {};
    const params = new URLSearchParams({ repo: repo || "" });
    if (language) params.set("language", language);
    try {
      const resp = await fetch(`${API_URL}/learnings/brief?${params.toString()}`);
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to fetch learnings: ${err}` }] };
      }
      const data = await resp.json();
      const brief = (data.brief || "").trim();
      const learnings = Array.isArray(data.learnings) ? data.learnings : [];
      if (!brief && learnings.length === 0) {
        return {
          content: [{
            type: "text",
            text: `No active learnings for ${repo}${language ? ` (language: ${language})` : ""} yet.`,
          }],
        };
      }
      const briefBlock = brief || "(no prose brief available)";
      const text =
        `Brief:\n${briefBlock}\n\n` +
        `(${learnings.length} structured learning${learnings.length === 1 ? "" : "s"} follow for programmatic use)`;
      return { content: [{ type: "text", text }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "maiko_get_conventions") {
    const { repo } = req.params.arguments || {};
    const params = new URLSearchParams({ repo: repo || "" });
    try {
      const resp = await fetch(`${API_URL}/insights/playbook?${params.toString()}`);
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to fetch conventions: ${err}` }] };
      }
      const data = await resp.json();
      const playbook = (data.playbook || "").trim();
      const insights = Array.isArray(data.insights) ? data.insights : [];
      const count = typeof data.count === "number" ? data.count : insights.length;
      if (!playbook) {
        return {
          content: [{
            type: "text",
            text: `No conventions registered for ${repo} yet.`,
          }],
        };
      }
      const text =
        `${playbook}\n\n` +
        `(${count} insight${count === 1 ? "" : "s"} underlying this playbook)`;
      return { content: [{ type: "text", text }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "maiko_get_adapter_info") {
    const { repo } = req.params.arguments || {};
    const params = new URLSearchParams({ repo: repo || "" });
    try {
      const resp = await fetch(`${API_URL}/lora/adapters?${params.toString()}`);
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to fetch adapter info: ${err}` }] };
      }
      const data = await resp.json();
      if (!data.exists) {
        return {
          content: [{
            type: "text",
            text: `No LoRA adapter has been trained for ${repo}.`,
          }],
        };
      }
      const version = data.version ? ` v${data.version}` : "";
      const base = data.base_model ? ` on base ${data.base_model}` : "";
      const trained = data.trained_at ? ` trained ${data.trained_at}` : "";
      const score = data.eval_score !== undefined && data.eval_score !== null
        ? `, eval score ${data.eval_score}`
        : "";
      const size = data.dataset_size !== undefined && data.dataset_size !== null
        ? `, dataset size ${data.dataset_size}`
        : "";
      const path = data.adapter_path ? `\nAdapter path: ${data.adapter_path}` : "";
      const text =
        `LoRA adapter for ${data.repo || repo}${version}${base}${trained}${score}${size}.${path}`;
      return { content: [{ type: "text", text }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "maiko_check_compliance") {
    const { diff, repo, scope } = req.params.arguments || {};
    const body = { diff, repo };
    if (scope) body.scope = scope;
    try {
      const resp = await fetch(`${API_URL}/lora/check-diff`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to run LoRA: ${err}` }] };
      }
      const data = await resp.json();
      if (data.no_model_for_repo) {
        return {
          content: [{
            type: "text",
            text: `No LoRA configured for ${data.repo || repo} — skip.`,
          }],
        };
      }
      if (data.no_changes) {
        return {
          content: [{ type: "text", text: "No changes in diff — nothing to review." }],
        };
      }
      const violations = data.violations || [];
      if (violations.length === 0) {
        return {
          content: [{ type: "text", text: "LoRA check: PASS. No violations detected." }],
        };
      }
      const formatted = violations
        .map((v, i) => `${i + 1}. [${v.category}] ${v.message}`)
        .join("\n");
      return {
        content: [{
          type: "text",
          text: `LoRA check found ${violations.length} violation(s):\n${formatted}\n\nAddress each one you agree with.`,
        }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "maiko_submit_feedback") {
    const { type, content, context = {} } = req.params.arguments || {};
    if (!type || !content) {
      return {
        content: [{ type: "text", text: "Error: type and content are required." }],
      };
    }

    let url;
    let body;

    if (type === "signal") {
      if (!context.category) {
        return {
          content: [{
            type: "text",
            text: "Error: context.category is required for type=signal.",
          }],
        };
      }
      url = `${API_URL}/signals`;
      body = {
        category: context.category,
        text: content,
        source_type: context.source_type || "external_mcp",
        severity: context.severity || "suggestion",
        repo: context.repo,
        language: context.language,
        file_path: context.file,
        code_context: context.code,
      };
    } else if (type === "false_positive") {
      url = `${API_URL}/lora/false_positive`;
      body = {
        code: content,
        file: context.file,
        category: context.category,
        repo: context.repo,
        reason: context.reason,
      };
    } else if (type === "false_negative") {
      if (!context.violation) {
        return {
          content: [{
            type: "text",
            text: "Error: context.violation is required for type=false_negative.",
          }],
        };
      }
      url = `${API_URL}/lora/false_negative`;
      body = {
        code: content,
        violation: context.violation,
        category: context.category,
        file: context.file,
        repo: context.repo,
      };
    } else {
      return {
        content: [{
          type: "text",
          text: `Error: unknown type "${type}". Use "signal", "false_positive", or "false_negative".`,
        }],
      };
    }

    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to submit feedback: ${err}` }] };
      }
      const data = await resp.json();
      const id = data.id !== undefined ? ` #${data.id}` : "";
      return {
        content: [{
          type: "text",
          text: `Submitted ${type}${id} — feeds the next training cycle.`,
        }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "maiko_submit_insight") {
    const { repo, text, tags, session_id } = req.params.arguments || {};
    if (!repo || !text) {
      return {
        content: [{ type: "text", text: "Error: repo and text are required." }],
      };
    }

    const body = {
      text,
      repo_scope: repo,
      tags: Array.isArray(tags) ? tags : [],
      status: "pending",
    };
    if (session_id) body.author_agent_id = session_id;

    try {
      const resp = await fetch(`${API_URL}/insights`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to submit insight: ${err}` }] };
      }
      const data = await resp.json();
      const id = data.id !== undefined ? ` #${data.id}` : "";
      const status = data.status || "pending";
      return {
        content: [{
          type: "text",
          text: `Submitted insight${id} (status: ${status}) — awaits user approval.`,
        }],
      };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  throw new Error(`unknown tool: ${req.params.name}`);
});

await mcp.connect(new StdioServerTransport());
