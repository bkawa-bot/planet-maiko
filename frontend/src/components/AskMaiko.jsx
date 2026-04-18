import { useState, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { MessageCircle, Send, X, Loader, ChevronDown, ChevronUp, ArrowRight } from "lucide-react";
import "./AskMaiko.css";

export default function AskMaiko() {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState([]);
  const [input, setInput] = useState("");
  const [context, setContext] = useState("");
  const [showContext, setShowContext] = useState(false);
  const [loading, setLoading] = useState(false);
  const messagesEnd = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, loading]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const send = async () => {
    const ask = input.trim();
    if (!ask || loading) return;

    const ctx = context.trim();
    setTurns((prev) => [...prev, { kind: "user", text: ask, context: ctx }]);
    setInput("");
    setContext("");
    setShowContext(false);
    setLoading(true);

    try {
      const res = await api.dispatchPack(ask, ctx);
      if (res.status === "clarify") {
        setTurns((prev) => [...prev, { kind: "clarify", text: res.clarify }]);
      } else if (res.status === "dispatched") {
        setTurns((prev) => [...prev, {
          kind: "dispatched",
          agent: res.agent,
          task: res.task,
          message: res.message,
          reasoning: res.reasoning,
          launchStatus: res.launch_status,
        }]);
      } else {
        setTurns((prev) => [...prev, { kind: "error", text: res.error || "Something went wrong." }]);
      }
    } catch (err) {
      setTurns((prev) => [...prev, { kind: "error", text: err.message }]);
    }
    setLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <>
      {!open && (
        <button className="ask-maiko-bubble" onClick={() => setOpen(true)} title="Ask the Pack">
          <MessageCircle size={20} />
        </button>
      )}

      {open && (
        <div className="ask-maiko-panel">
          <div className="ask-maiko-header">
            <span className="ask-maiko-title">Ask the Pack</span>
            <button className="ask-maiko-close" onClick={() => setOpen(false)}>
              <X size={14} />
            </button>
          </div>

          <div className="ask-maiko-messages">
            {turns.length === 0 && !loading && (
              <div className="ask-pack-intro">
                Tell the pack what you need — they'll pick the right agent and get on it.
              </div>
            )}

            {turns.map((turn, i) => (
              <PackTurn key={i} turn={turn} onClose={() => setOpen(false)} />
            ))}

            {loading && (
              <div className="ask-maiko-msg maiko">
                <span className="ask-maiko-avatar">M</span>
                <div className="ask-maiko-msg-text ask-maiko-typing">
                  <Loader size={12} className="spin" /> Finding the right agent…
                </div>
              </div>
            )}
            <div ref={messagesEnd} />
          </div>

          <div className="ask-pack-input-wrap">
            {showContext && (
              <textarea
                className="ask-pack-context"
                value={context}
                onChange={(e) => setContext(e.target.value)}
                placeholder="Optional context — URL, file path, deadline, anything that helps…"
                rows={2}
              />
            )}

            <div className="ask-maiko-input-row">
              <textarea
                ref={inputRef}
                className="ask-maiko-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="What can the pack help with?"
                rows={1}
              />
              <button
                className="ask-maiko-send"
                onClick={send}
                disabled={loading || !input.trim()}
                title="Send to the pack"
              >
                <Send size={14} />
              </button>
            </div>

            <button
              className="ask-pack-context-toggle"
              onClick={() => setShowContext((s) => !s)}
              type="button"
            >
              {showContext ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
              {showContext ? "Hide context" : "Add context"}
            </button>
          </div>
        </div>
      )}
    </>
  );
}


function PackTurn({ turn, onClose }) {
  if (turn.kind === "user") {
    return (
      <div className="ask-maiko-msg user">
        <div className="ask-maiko-msg-text">
          {turn.text}
          {turn.context && <div className="ask-pack-user-ctx">— {turn.context}</div>}
        </div>
      </div>
    );
  }

  if (turn.kind === "clarify") {
    return (
      <div className="ask-maiko-msg maiko">
        <span className="ask-maiko-avatar">M</span>
        <div className="ask-maiko-msg-text">{turn.text}</div>
      </div>
    );
  }

  if (turn.kind === "error") {
    return (
      <div className="ask-maiko-msg maiko">
        <span className="ask-maiko-avatar">M</span>
        <div className="ask-maiko-msg-text ask-pack-error">Hmm, that didn't work. {turn.text}</div>
      </div>
    );
  }

  const { agent, task, message, launchStatus } = turn;
  const agentInitial = (agent?.display_name || "M").charAt(0).toUpperCase();

  return (
    <div className="ask-pack-card">
      <div className="ask-pack-card-head">
        <span className="ask-pack-agent-avatar">{agentInitial}</span>
        <div className="ask-pack-card-msg">{message}</div>
      </div>
      <div className="ask-pack-task-row">
        <div className="ask-pack-task-title">{task?.title}</div>
        <div className="ask-pack-task-meta">
          {launchStatus === "queued" && <span className="ask-pack-chip">queued</span>}
          {launchStatus === "kicked_off" && <span className="ask-pack-chip ask-pack-chip-on">running</span>}
        </div>
      </div>
      <Link
        to="/agents"
        className="ask-pack-open-link"
        onClick={onClose}
      >
        Open in Agents <ArrowRight size={11} />
      </Link>
    </div>
  );
}
