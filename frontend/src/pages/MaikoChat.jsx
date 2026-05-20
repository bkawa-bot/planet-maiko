import { useEffect, useState, useRef } from "react";
import { api } from "../api/client";
import { Send, Loader } from "@icons";
import "./MaikoChat.css";

/**
 * Maiko's chat page — global conversation with the controller. Mirrors
 * an agent chat page in shell but the backend is /api/maiko/* (single
 * thread, no task_id, read-only access to pack state for now).
 *
 * Entry points: the slot at the top of the Active agents tab, and
 * eventually a persistent presence in the topbar.
 */
export default function MaikoChat() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    api.getMaikoMessages()
      .then((rows) => setMessages(Array.isArray(rows) ? rows : []))
      .catch(() => setMessages([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, sending]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const send = async () => {
    const content = input.trim();
    if (!content || sending) return;
    setInput("");
    setSending(true);
    // Optimistic append so the user sees their message before the
    // reply lands. Server-saved version replaces it on response.
    const tmpId = `tmp-${Date.now()}`;
    const optimistic = { id: tmpId, role: "user", content, created_at: new Date().toISOString() };
    setMessages((prev) => [...prev, optimistic]);
    try {
      const r = await api.sendMaikoMessage(content);
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== tmpId),
        r.user,
        r.maiko,
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev.filter((m) => m.id !== tmpId),
        optimistic,
        {
          id: `err-${Date.now()}`,
          role: "maiko",
          content: `Couldn't reach me: ${err.message}`,
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      // Cmd/Ctrl+Enter inserts a newline manually since the browser
      // default for that combo is "nothing" in a textarea.
      const el = e.target;
      const start = el.selectionStart;
      const end = el.selectionEnd;
      setInput(el.value.slice(0, start) + "\n" + el.value.slice(end));
      requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = start + 1; });
      e.preventDefault();
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="maiko-chat-page">
      <header className="maiko-chat-header">
        <img src="/icon.svg" alt="" className="maiko-chat-avatar" />
        <div>
          <h1 className="maiko-chat-title">Maiko</h1>
          <div className="maiko-chat-sub">she sees the pack. ask her anything.</div>
        </div>
      </header>

      <div className="maiko-chat-thread">
        {loading && (
          <div className="maiko-chat-empty">Loading…</div>
        )}
        {!loading && messages.length === 0 && (
          <div className="maiko-chat-empty">
            Nothing here yet. Ask what an agent is up to, what automations are set, what tasks are open, or whatever else.
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`maiko-msg maiko-msg-${m.role}`}>
            <div className="maiko-msg-body">{m.content}</div>
          </div>
        ))}
        {sending && (
          <div className="maiko-msg maiko-msg-maiko maiko-msg-typing">
            <Loader size={12} className="spin" /> thinking…
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="maiko-chat-input-wrap">
        <textarea
          ref={inputRef}
          className="maiko-chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask Maiko anything. Shift or Cmd+Enter for a newline."
          rows={2}
          disabled={sending}
        />
        <button
          className="maiko-chat-send"
          onClick={send}
          disabled={sending || !input.trim()}
          title="Send"
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
