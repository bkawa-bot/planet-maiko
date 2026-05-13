import { useCallback, useEffect, useRef, useState } from "react";
import { MessageSquare, Send } from "@icons";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { formatTime } from "../utils/dates";

const CHAT_POLL_INTERVAL_MS = 8000;

/**
 * Channel log + reply box for one agent. Used by both ReviewPlan
 * (chatting with a coding agent during plan review) and JobReport
 * (chatting with a one-shot job agent after they reported back).
 *
 * `id` is whatever the agent reported with — task id for tasks,
 * AgentJob id for one-shot jobs. AgentMessage.task_id is shaped to
 * accept either; the inbox / messages endpoints look up by that
 * single id space.
 *
 * The CSS classes reuse the `review-plan-chat-*` selectors that
 * already exist in ReviewPlan.css. When the chat surface eventually
 * lives somewhere besides those two pages, this is the place to
 * generalize the styling too.
 */
export default function AgentChatThread({
  id,
  hint = "For follow-up questions and clarifications.",
  emptyMessage = "No messages yet. Ask the agent something — they'll respond on their next check-in.",
}) {
  const [messages, setMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [sendingChat, setSendingChat] = useState(false);
  const chatEndRef = useRef(null);

  const fetchMessages = useCallback(async () => {
    if (!id) return;
    try {
      const msgs = await api.getAgentMessages(id);
      setMessages(msgs || []);
    } catch {
      // Chat is non-critical — keep the surrounding page usable if
      // a poll blips (network hiccup, stale auth, etc).
    }
  }, [id]);

  useEffect(() => {
    fetchMessages();
    const interval = setInterval(fetchMessages, CHAT_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchMessages]);

  // Scroll to the newest message whenever the thread grows.
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  const handleSendChat = async () => {
    const text = chatInput.trim();
    if (!text || sendingChat || !id) return;
    setSendingChat(true);
    try {
      // Backend auto-wakes when sender=user and returns wake_mode so we
      // can tell the user whether the agent was actually woken, queued
      // behind a current run, or has no resumable session at all. Same
      // shape as AgentsActiveTab — keep the surfaces consistent so users
      // know whether their message will actually be read.
      const res = await api.sendToAgent(id, { content: text, sender: "user" });
      const mode = res?.wake_mode;
      if (mode === "woke") showToast("Message sent — waking the agent ✨", "normal");
      else if (mode === "queued") showToast("Agent's working — queued for the next turn", "normal");
      else if (mode === "error") showToast("Sent, but agent has no live session to wake", "high");
      else showToast("Message saved to inbox", "normal");
      setChatInput("");
      await fetchMessages();
    } catch (err) {
      showToast(err.message || "Couldn't send message", "high");
    } finally {
      setSendingChat(false);
    }
  };

  return (
    <div className="review-plan-chat">
      <div className="review-plan-chat-header">
        <MessageSquare size={12} /> Chat with the agent
        {hint && <span className="review-plan-chat-hint">{hint}</span>}
      </div>
      <div className="review-plan-chat-thread">
        {messages.length === 0 ? (
          <div className="review-plan-chat-empty">{emptyMessage}</div>
        ) : (
          messages.map((m) => (
            <div key={m.id} className={`review-plan-chat-msg ${m.direction}`}>
              <div className="review-plan-chat-msg-meta">
                <span className="review-plan-chat-sender">{m.sender}</span>
                <span className="review-plan-chat-type">{m.message_type}</span>
                <span className="review-plan-chat-time">{formatTime(m.created_at)}</span>
              </div>
              <div className="review-plan-chat-content">{m.content}</div>
            </div>
          ))
        )}
        <div ref={chatEndRef} />
      </div>
      <div className="review-plan-chat-input">
        <input
          type="text"
          value={chatInput}
          onChange={(e) => setChatInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) handleSendChat(); }}
          placeholder="Send a message…"
          disabled={sendingChat}
        />
        <button
          className="btn btn-primary btn-sm"
          onClick={handleSendChat}
          disabled={sendingChat || !chatInput.trim()}
        >
          <Send size={11} /> Send
        </button>
      </div>
    </div>
  );
}
