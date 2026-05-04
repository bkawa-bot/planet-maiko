import { useState } from "react";
import { api } from "../../api/client";
import { showToast } from "../Toast";



/** Inline reply box on agent_stuck memos. Lets the user unblock
 *  the agent without leaving Home — type, send, the agent's wake
 *  hook fires, the memo gets dismissed, the row drops out. The
 *  endpoint (POST /agents/<task_id>/inbox with sender="user")
 *  already auto-wakes the agent's session, so the user doesn't
 *  need to do anything else after sending. */
export default function StuckReplyBox({ taskId, memoId, onReplied }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);

  const send = async () => {
    const body = text.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      await api.sendToAgent(taskId, { content: body, sender: "user" });
      // Dismiss the memo — the user just answered the question, no
      // reason to keep nudging. The agent's reply will surface as a
      // fresh memo when they have one.
      if (memoId) {
        try { await api.dismissMemo(memoId); } catch { /* non-fatal */ }
      }
      showToast("Reply sent — agent's been woken", "normal");
      setText("");
      onReplied?.();
    } catch (err) {
      showToast("Couldn't send: " + (err.message || "unknown"), "high");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="memos-stuck-reply">
      <textarea
        className="memos-stuck-reply-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // Cmd/Ctrl-Enter sends. Plain Enter newlines so multi-line
          // unblocks (paste a stack trace, link, etc.) work.
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            send();
          }
        }}
        placeholder="Reply to unblock the agent…"
        rows={3}
      />
      <div className="memos-stuck-reply-actions">
        <span className="memos-stuck-reply-hint">
          ⌘/Ctrl + Enter to send
        </span>
        <button
          className="btn btn-sm btn-primary"
          disabled={!text.trim() || sending}
          onClick={send}
        >
          {sending ? "Sending…" : "Send"}
        </button>
      </div>
    </div>
  );
}