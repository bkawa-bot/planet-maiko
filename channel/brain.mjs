#!/usr/bin/env node
/**
 * Maiko Brain — MCP server for external orchestrators to query Planet Maiko.
 *
 * Sibling to index.mjs. While index.mjs serves LLM agents Maiko spawns inside
 * its own worktrees (task-scoped, push-notification channel), this server
 * serves external tools running their own LLM sessions so they can register
 * those sessions with Maiko for agent-to-agent conflict detection.
 *
 * Phase A + B: three A2A conflict-detection tools plus three read-surface
 * tools (learnings, conventions, adapter info). Compliance and producer
 * hooks come later.
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
      `You have six tools for coordinating an external LLM session with Planet Maiko:`,
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
      ``,
      `Call maiko_register_session once at the start of a task,`,
      `maiko_detect_conflicts before and during edits to catch overlap with`,
      `other agents, and maiko_complete_session once the task ends. Fetch the`,
      `read-surface tools (learnings / conventions / adapter info) once at`,
      `session start and reuse the results — they only change at brain-cycle`,
      `cadence or on user approvals.`,
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

  throw new Error(`unknown tool: ${req.params.name}`);
});

await mcp.connect(new StdioServerTransport());
