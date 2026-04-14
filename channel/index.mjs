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
 *   MAIKO_POLL_MS  — Polling interval in ms (default: 60000)
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
const POLL_MS = parseInt(process.env.MAIKO_POLL_MS || "60000", 10);

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
      `You have three tools for talking to Planet Maiko:`,
      `- reply: send a message back to Maiko / the user (status, done,`,
      `  stuck, ready_for_review, message)`,
      `- check_inbox: pull any pending messages from Maiko / the user`,
      `- leave_comment: pin an inline comment to a specific diff line`,
      `  for the user to see while reviewing your changes`,
      ``,
      `The user can send you messages from the Channel Log at any time.`,
      `Those messages accumulate in your inbox until you read them.`,
      `When the user asks a follow-up question or sends a nudge, call`,
      `check_inbox to see what's new, then reply to what you find.`,
      ``,
      `Guidelines:`,
      `- Coding agents: after your first meaningful commit, call reply`,
      `  with message_type="ready_for_review" summarizing what you did.`,
      `  Then loop: check_inbox every ~30s; on a message_type="review"`,
      `  message, iterate on the comments, commit, and reply ready again.`,
      `- Use leave_comment sparingly (~5 max per review round) on`,
      `  uncertain or load-bearing lines you want human eyes on.`,
      `- If you're stuck, use reply with message_type="stuck".`,
      `- Check your inbox whenever you're about to end a response or`,
      `  wait for input — there may be a message queued.`,
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
        "Send a message back to Planet Maiko (status update, review request, task completion, or help request)",
      inputSchema: {
        type: "object",
        properties: {
          content: {
            type: "string",
            description: "The message to send back to Maiko",
          },
          message_type: {
            type: "string",
            enum: ["message", "status", "feedback", "done", "stuck", "ready_for_review"],
            description:
              "Type of message: " +
              "'message' for general replies to the user, " +
              "'status' for live progress updates (chatter, no pupdate), " +
              "'feedback' to record a learning / training signal, " +
              "'ready_for_review' when you've committed work and the user should review the diff, " +
              "'done' for task completion, " +
              "'stuck' when you're blocked and need the user's help.",
          },
        },
        required: ["content"],
      },
    },
    {
      name: "check_inbox",
      description:
        "Pull any pending messages the user or Maiko has sent to you. " +
        "Returns an array of { sender, content, message_type } objects. " +
        "Messages are marked read on retrieval — call this before ending " +
        "a response or whenever you suspect the user has replied.",
      inputSchema: {
        type: "object",
        properties: {
          unread_only: {
            type: "boolean",
            description:
              "If true (default), only return unread messages and mark them read. " +
              "Set false to see the full thread history without side effects.",
          },
        },
      },
    },
    {
      name: "leave_comment",
      description:
        "Pin an inline comment to a specific diff line for the user to " +
        "see while reviewing your changes. Use this sparingly on lines " +
        "that are uncertain, load-bearing, or deserve a second pair of " +
        "eyes. Comments appear in the Review Diff page alongside the " +
        "user's own comments but styled distinctly.",
      inputSchema: {
        type: "object",
        properties: {
          file_path:   { type: "string", description: "Path from the repo root (same as in the diff)" },
          line_number: { type: "integer", description: "Line number in the file" },
          side:        {
            type: "string",
            enum: ["old", "new"],
            description: "Which side of the diff. \"new\" for added / modified code (default), \"old\" for removed lines.",
          },
          body:        { type: "string", description: "The comment body (markdown supported)" },
        },
        required: ["file_path", "line_number", "body"],
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

  if (req.params.name === "leave_comment") {
    const { file_path, line_number, side = "new", body } = req.params.arguments;
    try {
      const resp = await fetch(`${API_URL}/tasks/${TASK_ID}/comments/agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path, line_number, side, body }),
      });
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to leave comment: ${err}` }] };
      }
      return { content: [{ type: "text", text: `Comment pinned to ${file_path}:${line_number}` }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "check_inbox") {
    const unreadOnly = req.params.arguments?.unread_only !== false;
    const url = `${API_URL}/agents/${TASK_ID}/inbox?unread_only=${unreadOnly}&mark_read=${unreadOnly}`;
    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        const err = await resp.text();
        return {
          content: [{ type: "text", text: `Failed to read inbox: ${err}` }],
        };
      }
      const messages = await resp.json();
      if (!messages.length) {
        return {
          content: [{ type: "text", text: "No messages. Inbox is empty." }],
        };
      }
      // Return as structured text the agent can reason over directly.
      const formatted = messages
        .map((m) => {
          const who = m.sender || "user";
          const mt = m.message_type || "message";
          return `[${who} · ${mt}] ${m.content}`;
        })
        .join("\n\n");
      return {
        content: [{ type: "text", text: formatted }],
      };
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
