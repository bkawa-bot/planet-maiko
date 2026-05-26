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
  const [loading, setLoading] = useState(true);
  const endRef = useRef(null);
  const inputRef = useRef(null);
  const initialScrolled = useRef(false);

  useEffect(() => {
    api.getMaikoMessages()
      .then((rows) => setMessages(Array.isArray(rows) ? rows : []))
      .catch(() => setMessages([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!endRef.current) return;
    // First time messages land, jump straight to the bottom (no
    // animation — the user just opened the chat, they want to be at
    // the most recent already, not watch the page scroll through
    // their history). Subsequent updates (a new message, a "thinking"
    // bubble, a reply replacing a placeholder) animate so the user
    // sees them arrive.
    endRef.current.scrollIntoView({
      behavior: initialScrolled.current ? "smooth" : "auto",
    });
    if (messages.length > 0) initialScrolled.current = true;
  }, [messages]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const send = async () => {
    const content = input.trim();
    if (!content) return;
    setInput("");
    // Each send is independent: optimistic user bubble + a per-send
    // "thinking" placeholder. The server reply replaces the
    // placeholder in place, so multiple sends can be in flight at
    // once and the user can keep typing follow-ups without waiting
    // for Maiko's LLM round trip (10–30s) to finish.
    const stamp = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const tmpUserId = `tmp-user-${stamp}`;
    const tmpMaikoId = `tmp-maiko-${stamp}`;
    const optimisticUser = {
      id: tmpUserId,
      role: "user",
      content,
      created_at: new Date().toISOString(),
    };
    const thinking = {
      id: tmpMaikoId,
      role: "maiko",
      content: "",
      _pending: true,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUser, thinking]);
    try {
      const r = await api.sendMaikoMessage(content);
      setMessages((prev) => prev.map((m) => {
        if (m.id === tmpUserId) return r.user;
        if (m.id === tmpMaikoId) return r.maiko;
        return m;
      }));
    } catch (err) {
      setMessages((prev) => prev.map((m) => {
        if (m.id === tmpMaikoId) {
          return {
            id: `err-${stamp}`,
            role: "maiko",
            content: `Couldn't reach me: ${err.message}`,
            created_at: new Date().toISOString(),
          };
        }
        return m;
      }));
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
          <div
            key={m.id}
            className={`maiko-msg maiko-msg-${m.role}${m._pending ? " maiko-msg-typing" : ""}`}
          >
            {m._pending ? (
              <><Loader size={12} className="spin" /> thinking…</>
            ) : (
              <div className="maiko-msg-body">{m.content}</div>
            )}
          </div>
        ))}
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
        />
        <button
          className="maiko-chat-send"
          onClick={send}
          disabled={!input.trim()}
          title="Send"
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
