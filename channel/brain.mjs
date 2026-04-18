#!/usr/bin/env node
/**
 * Maiko Brain — MCP server for external orchestrators to query Planet Maiko.
 *
 * Sibling to index.mjs. While index.mjs serves LLM agents Maiko spawns inside
 * its own worktrees (task-scoped, push-notification channel), this server
 * serves external tools running their own LLM sessions so they can register
 * those sessions with Maiko for agent-to-agent conflict detection.
 *
 * Phase A: just the three A2A conflict-detection tools. Read surface,
 * compliance, and producer hooks come later.
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
      `You have three tools for coordinating an external LLM session with Planet Maiko:`,
      `- maiko_register_session(repo, worktree_path, session_id?, hint?) — announce`,
      `  that a new session is starting work in a given repo/worktree. Returns`,
      `  the session_id Maiko will use to track it for conflict detection.`,
      `- maiko_detect_conflicts(session_id) — list any active conflicts between`,
      `  this session and other registered sessions (overlapping files, etc).`,
      `  Safe to call repeatedly; pure query, no side effects.`,
      `- maiko_complete_session(session_id, outcome?) — mark the session finished`,
      `  so Maiko can stop tracking it for conflicts.`,
      ``,
      `Call maiko_register_session once at the start of a task,`,
      `maiko_detect_conflicts before and during edits to catch overlap with`,
      `other agents, and maiko_complete_session once the task ends.`,
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

  throw new Error(`unknown tool: ${req.params.name}`);
});

await mcp.connect(new StdioServerTransport());
