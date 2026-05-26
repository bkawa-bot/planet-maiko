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
  // FIFO queue of thinking-placeholder ids waiting for a real reply.
  // Each send pushes one; refetch pops the oldest when a new maiko
  // message arrives in a poll, and swaps it in place.
  const pendingMaikoIds = useRef([]);

  useEffect(() => {
    api.getMaikoMessages()
      .then((rows) => setMessages(Array.isArray(rows) ? rows : []))
      .catch(() => setMessages([]))
      .finally(() => setLoading(false));
  }, []);

  const refetchMessages = async () => {
    try {
      const rows = await api.getMaikoMessages();
      setMessages((prev) => {
        // Real (server-persisted) rows the UI already knows about.
        // Optimistic / pending ids are non-numeric so they don't get
        // counted here.
        const realIds = new Set(
          prev.filter((m) => typeof m.id === "number").map((m) => m.id),
        );
        const newRows = rows.filter((r) => !realIds.has(r.id));
        if (newRows.length === 0) return prev;

        const next = [...prev];
        for (const row of newRows) {
          // A new maiko row fulfills the oldest queued thinking
          // placeholder. Out-of-band maiko rows (e.g. seeded from
          // another tab) just append.
          if (row.role === "maiko" && pendingMaikoIds.current.length > 0) {
            const tmpId = pendingMaikoIds.current.shift();
            const idx = next.findIndex((m) => m.id === tmpId);
            if (idx >= 0) {
              next[idx] = row;
              continue;
            }
          }
          if (!next.some((m) => m.id === row.id)) {
            next.push(row);
          }
        }
        return next;
      });
    } catch {
      // Transient failures keep the prior list.
    }
  };

  // Poll for new messages while the page is open. 4s is snappier
  // than the 8s AgentJobPage uses because this surface is an
  // active conversation, not a background-watching audit log.
  useEffect(() => {
    if (loading) return undefined;
    const id = setInterval(refetchMessages, 4000);
    return () => clearInterval(id);
  }, [loading]);

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
    // "thinking" placeholder. POST returns the saved user message
    // immediately; the maiko reply arrives via the polling tick
    // after the background generation finishes (10-90s). Multiple
    // sends can be in flight at once — each placeholder gets queued
    // in pendingMaikoIds and the next polled maiko reply fulfills
    // the oldest one FIFO.
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
    pendingMaikoIds.current.push(tmpMaikoId);
    try {
      const r = await api.sendMaikoMessage(content);
      // POST now returns just {user}; the reply will arrive via the
      // polling tick after Maiko finishes generating.
      setMessages((prev) => prev.map((m) => {
        if (m.id === tmpUserId) return r.user;
        return m;
      }));
    } catch (err) {
      // POST failed (network, validation, etc). Drop the queued
      // placeholder so a future unrelated reply can't claim it, and
      // swap the thinking bubble for the error.
      pendingMaikoIds.current = pendingMaikoIds.current.filter(
        (id) => id !== tmpMaikoId,
      );
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
