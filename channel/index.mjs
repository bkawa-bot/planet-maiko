#!/usr/bin/env node
/**
 * Maiko Channel — MCP channel server for Planet Maiko agent communication.
 *
 * Pushes messages from the Maiko API into a Claude Code session in real-time,
 * replacing the need for agents to poll `maiko inbox`.
 *
 * Environment variables:
 *   MAIKO_API_URL  — Planet Maiko API base URL (default: http://localhost:8420/api)
 *   MAIKO_TASK_ID  — The task ID this agent is working on (required)
 *   MAIKO_POLL_MS  — Polling interval in ms (default: 15000)
 *
 * Usage:
 *   MAIKO_TASK_ID=task-123 claude --dangerously-load-development-channels server:maiko-channel
 *
 * Or in .mcp.json:
 *   {
 *     "mcpServers": {
 *       "maiko-channel": {
 *         "command": "node",
 *         "args": ["./channel/index.js"],
 *         "env": { "MAIKO_TASK_ID": "task-123" }
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
const TASK_ID = process.env.MAIKO_TASK_ID;
const POLL_MS = parseInt(process.env.MAIKO_POLL_MS || "15000", 10);

if (!TASK_ID) {
  console.error("MAIKO_TASK_ID environment variable is required");
  process.exit(1);
}

// Track which messages we've already pushed to avoid duplicates
const seenMessageIds = new Set();

const mcp = new Server(
  { name: "maiko-channel", version: "0.1.0" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
      tools: {},
    },
    instructions: [
      `Messages from Planet Maiko arrive as <channel source="maiko-channel" ...>.`,
      `These are messages from Maiko (the orchestrator) or the user.`,
      `When you receive a channel message:`,
      `- If it's a question, respond using the reply tool`,
      `- If it's a nudge/heartbeat, report your current status using the reply tool`,
      `- If it's a new instruction, follow it`,
      `- If it's a sleep signal, stop work and wait`,
      `You can also proactively report status using the reply tool at any time.`,
      `Your task ID is: ${TASK_ID}`,
    ].join("\n"),
  }
);

// --- Two-way: reply tool ---

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "reply",
      description:
        "Send a message back to Planet Maiko (status update, question, or task completion)",
      inputSchema: {
        type: "object",
        properties: {
          content: {
            type: "string",
            description: "The message to send back to Maiko",
          },
          message_type: {
            type: "string",
            enum: ["message", "status", "feedback", "done", "stuck"],
            description:
              "Type of message: 'status' for updates, 'done' for task completion, 'stuck' for help requests",
          },
        },
        required: ["content"],
      },
    },
  ],
}));

mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
  if (req.params.name === "reply") {
    const { content, message_type = "message" } = req.params.arguments;

    try {
      const resp = await fetch(`${API_URL}/agents/${TASK_ID}/outbox`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          sender: "agent",
          message_type,
        }),
      });

      if (!resp.ok) {
        const err = await resp.text();
        return {
          content: [{ type: "text", text: `Failed to send: ${err}` }],
        };
      }

      // If task is done, also update task status
      if (message_type === "done") {
        await fetch(`${API_URL}/tasks/${TASK_ID}/done`, { method: "POST" });
      }

      return { content: [{ type: "text", text: "sent" }] };
    } catch (err) {
      return {
        content: [{ type: "text", text: `Error: ${err.message}` }],
      };
    }
  }

  throw new Error(`unknown tool: ${req.params.name}`);
});

// --- Polling: check for new messages and push them ---

async function pollMessages() {
  try {
    const resp = await fetch(
      `${API_URL}/agents/${TASK_ID}/inbox?unread_only=true&mark_read=true`
    );
    if (!resp.ok) return;

    const messages = await resp.json();

    for (const msg of messages) {
      if (seenMessageIds.has(msg.id)) continue;
      seenMessageIds.add(msg.id);

      await mcp.notification({
        method: "notifications/claude/channel",
        params: {
          content: msg.content,
          meta: {
            sender: msg.sender || "maiko",
            message_type: msg.message_type || "message",
            message_id: String(msg.id),
            task_id: TASK_ID,
          },
        },
      });
    }
  } catch (err) {
    // Silently retry on next poll — Maiko server might not be running yet
  }
}

// --- Report session ID ---

async function reportSessionId() {
  const sessionId = process.env.CLAUDE_SESSION_ID;
  if (!sessionId || !TASK_ID) return;

  try {
    await fetch(`${API_URL}/agents/${TASK_ID}/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch (err) {
    // Non-blocking
  }
}

// --- Start ---

await mcp.connect(new StdioServerTransport());

// Report session ID to Maiko
reportSessionId();

// Start polling for messages
setInterval(pollMessages, POLL_MS);

// Initial poll
pollMessages();
