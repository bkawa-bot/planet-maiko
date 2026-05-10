#!/usr/bin/env node
/**
 * Maiko Channel — MCP channel server for Planet Maiko agent communication.
 *
 * Pushes messages from the Maiko API into a Claude Code session in real-time,
 * replacing the need for agents to poll `maiko inbox`.
 *
 * Environment variables:
 *   MAIKO_API_URL  — Planet Maiko API base URL (default: http://localhost:8420/api)
 *   MAIKO_JOB_ID   — The AgentJob ID this agent is working on (required;
 *                    MAIKO_TASK_ID is accepted as a transitional alias)
 *   MAIKO_POLL_MS  — Polling interval in ms (default: 60000)
 *
 * Usage:
 *   MAIKO_JOB_ID=job-abc123 claude --dangerously-load-development-channels server:maiko-channel
 *
 * Or in .mcp.json:
 *   {
 *     "mcpServers": {
 *       "maiko-channel": {
 *         "command": "node",
 *         "args": ["./channel/index.js"],
 *         "env": { "MAIKO_JOB_ID": "job-abc123" }
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
// MAIKO_JOB_ID is canonical post-rename; MAIKO_TASK_ID kept as a
// transitional fallback for sessions whose .mcp.json was written
// before the rename.
const JOB_ID = process.env.MAIKO_JOB_ID || process.env.MAIKO_TASK_ID;
const POLL_MS = parseInt(process.env.MAIKO_POLL_MS || "60000", 10);

if (!JOB_ID) {
  console.error("MAIKO_JOB_ID (or MAIKO_TASK_ID) environment variable is required");
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
      `- reply(content, message_type) — send a message back to Maiko / the`,
      `  user. The message body goes in the "content" parameter (NOT`,
      `  "message" or "body" — those will be rejected). Example:`,
      `      reply(content="Done with the auth refactor.", message_type="ready_for_review")`,
      `- check_inbox(unread_only?) — pull any pending messages from Maiko`,
      `  or the user.`,
      `- leave_comment(file_path, line_number, body) — pin an inline`,
      `  comment to a specific diff line for the user to see while`,
      `  reviewing your changes.`,
      ``,
      `The user can send you messages from the Channel Log at any time.`,
      `Those messages accumulate in your inbox until you read them.`,
      `When the user asks a follow-up question or sends a nudge, call`,
      `check_inbox to see what's new, then reply to what you find.`,
      ``,
      `Guidelines:`,
      `- Before declaring ready_for_review, run check_code(). It runs`,
      `  the mechanical checks (tests/lint/typecheck) auto-detected`,
      `  from your repo and returns a verdict. It is dishonest to claim`,
      `  you are done if the verdict is red — address failures first,`,
      `  re-run, then reply ready.`,
      `- Coding agents: after your first meaningful commit, call`,
      `  check_code() then reply(content="<summary>", message_type="ready_for_review").`,
      `  Then loop: check_inbox every ~30s; on a message_type="review"`,
      `  message, iterate on the comments, commit, and reply ready again.`,
      `- Use leave_comment sparingly (~5 max per review round) on`,
      `  uncertain or load-bearing lines you want human eyes on.`,
      `- If you're stuck, call reply(content="<what's blocking>", message_type="stuck").`,
      `- If you learn something about *how to work in this repo* that a`,
      `  future agent would benefit from (tooling quirks, migration state,`,
      `  team conventions, useful Slack channels), call reply with`,
      `  message_type="insight". These get injected into every new agent's`,
      `  CLAUDE.md — do NOT use this for coding rules (use "feedback"`,
      `  for those). One insight per call, keep it to a sentence.`,
      `- Check your inbox whenever you're about to end a response or`,
      `  wait for input — there may be a message queued.`,
      `Your job ID is: ${JOB_ID}`,
    ].join("\n"),
  }
);

// --- Two-way: reply tool ---

mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "reply",
      description:
        "Send a message back to Planet Maiko (status update, review request, " +
        "or help request). The message body goes in the REQUIRED `content` " +
        "parameter — NOT `message` or `body` (those will fail). Example: " +
        "reply(content=\"All tests pass.\", message_type=\"ready_for_review\")",
      inputSchema: {
        type: "object",
        properties: {
          content: {
            type: "string",
            description:
              "REQUIRED. The full message body / report text. Pass your " +
              "actual message string here. Do NOT use 'message' or 'body' " +
              "as the parameter name — only 'content' is accepted.",
          },
          message_type: {
            type: "string",
            enum: ["message", "status", "feedback", "insight", "stuck", "ready_for_review", "plan_for_approval", "pr_opened"],
            description:
              "Type of message: " +
              "'message' for general replies to the user, " +
              "'status' for live progress updates (chatter, no pupdate), " +
              "'feedback' to record a learning / training signal (coding rule — surfaces to future agents via rules-relevant), " +
              "'insight' to share tribal / operational knowledge that future agents should know (e.g. \"use IntelliJ to run tests\", \"the personalization repo is mid-migration\") — these get injected into every agent's CLAUDE.md, NOT trained on. One fact per reply. Reserve 'feedback' for coding rules and 'insight' for workflow / tooling / team context. " +
              "'plan_for_approval' when the task was started in plan mode and you've produced a markdown plan for the user to approve before you implement, " +
              "'ready_for_review' when you've committed work and the user should review the diff, " +
              "'pr_opened' after you've run `gh pr create` in response to an approved message — put the PR URL on its own line in the content, " +
              "'stuck' when you're blocked and need the user's help. " +
              "There is no 'done' — agents don't decide completion. Use 'ready_for_review' and let the user close the task after reviewing.",
          },
          recipient: {
            type: "string",
            enum: ["user"],
            description:
              "Optional. Set to 'user' when this message is specifically " +
              "for the user to read — Maiko surfaces it in the user's " +
              "memos/inbox so they see it without having to open the " +
              "task chat. Leave unset for in-thread chatter (status " +
              "updates, mid-run progress, anything the user can pick up " +
              "later by scrolling the thread). Use sparingly — every " +
              "user-targeted message is an interruption.",
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
      name: "check_code",
      description:
        "Run the mechanical checks for your worktree (tests, linters, " +
        "typechecker — auto-detected from the repo via pyproject.toml, " +
        "package.json, Cargo.toml, go.mod, or configured via " +
        "`.maiko/checks.json`) and return a verdict. Call this BEFORE " +
        "declaring ready_for_review. If anything fails, address the " +
        "failures and re-run before replying ready.",
      inputSchema: {
        type: "object",
        properties: {
          timeout: {
            type: "integer",
            description: "Per-check timeout in seconds. Defaults to 120. Increase if your test suite is slow.",
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
    const { content, message_type = "message", recipient } = req.params.arguments;

    try {
      const resp = await fetch(`${API_URL}/agents/${JOB_ID}/outbox`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          sender: "agent",
          message_type,
          // Backend coerces empty / undefined to null; only "user" has
          // current semantics. Future values land here unchanged.
          recipient: recipient || null,
        }),
      });

      if (!resp.ok) {
        const err = await resp.text();
        return {
          content: [{ type: "text", text: `Failed to send: ${err}` }],
        };
      }

      // No auto-close on message_type="done". Agents don't decide when
      // a task is complete — the user does (via the UI close button) or
      // the pr_merged automation does (when the linked PR lands). The
      // protocol retired the agent-side "done" exit; this server-side
      // call would delete the task row outright (/tasks/<id>/done is
      // destructive), which is a foot-gun nobody wants.

      return { content: [{ type: "text", text: "sent" }] };
    } catch (err) {
      return {
        content: [{ type: "text", text: `Error: ${err.message}` }],
      };
    }
  }

  if (req.params.name === "check_code") {
    const { timeout = 120 } = req.params.arguments || {};
    try {
      const resp = await fetch(`${API_URL}/checks/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: JOB_ID, timeout }),
      });
      if (!resp.ok) {
        const err = await resp.text();
        return { content: [{ type: "text", text: `Failed to run checks: ${err}` }] };
      }
      const data = await resp.json();
      const checks = data.checks || [];
      const summary = data.summary || {};

      const lines = [];

      // Mechanical checks (tests, linters, typecheckers).
      if (checks.length) {
        lines.push(`Mechanical checks: ${summary.passed ?? 0}/${summary.total ?? 0} passed.`);
        for (const c of checks) {
          const mark = c.status === "pass" ? "OK " : c.status === "fail" ? "FAIL" : "?";
          lines.push(`  [${mark}] ${c.name} (${c.status}${c.exit_code != null ? `, exit=${c.exit_code}` : ""})`);
          if (c.status !== "pass" && c.output_tail) {
            lines.push(c.output_tail.split("\n").map(l => `      ${l}`).join("\n"));
          }
        }
      } else {
        lines.push("Mechanical checks: none detected. Add a `.maiko/checks.json` with the commands you want agents to run, or ensure the repo has pyproject.toml + tests/, package.json with a test script, Cargo.toml, or go.mod.");
      }

      if (summary.blocked) {
        lines.push("");
        lines.push("Do NOT declare ready_for_review yet — address the failures first, then re-run check_code.");
      }

      return { content: [{ type: "text", text: lines.join("\n") }] };
    } catch (err) {
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }

  if (req.params.name === "leave_comment") {
    const { file_path, line_number, side = "new", body } = req.params.arguments;
    try {
      const resp = await fetch(`${API_URL}/tasks/${JOB_ID}/comments/agent`, {
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
    const url = `${API_URL}/agents/${JOB_ID}/inbox?unread_only=${unreadOnly}&mark_read=${unreadOnly}`;
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
    // mark_read=false: the channel notification is best-effort (it relies
    // on an experimental MCP channel that may not surface to the model).
    // If we marked read here and the notification got dropped, the Stop
    // hook would see an empty inbox and the message would be lost.
    // Leaving messages unread lets the Stop hook catch anything the
    // notification channel missed — that's the guaranteed delivery path.
    const resp = await fetch(
      `${API_URL}/agents/${JOB_ID}/inbox?unread_only=true&mark_read=false`
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
            job_id: JOB_ID,
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
  if (!sessionId || !JOB_ID) return;

  try {
    await fetch(`${API_URL}/agents/${JOB_ID}/session`, {
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
