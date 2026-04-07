import { useState, useRef, useEffect } from "react";
import { api } from "../api/client";
import { MessageCircle, Send, X, Loader } from "lucide-react";
import "./AskMaiko.css";

export default function AskMaiko() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([
    { role: "maiko", text: "Hey! I'm Maiko. Ask me anything about your tasks, agents, or what's going on." },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEnd = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const send = async () => {
    const text = input.trim();
    if (!text || loading) return;

    setMessages((prev) => [...prev, { role: "user", text }]);
    setInput("");
    setLoading(true);

    try {
      const res = await api.chat(text);
      setMessages((prev) => [...prev, { role: "maiko", text: res.response }]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "maiko", text: `Hmm, I couldn't process that. ${err.message}` },
      ]);
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
      {/* Floating bubble */}
      {!open && (
        <button className="ask-maiko-bubble" onClick={() => setOpen(true)} title="Ask Maiko">
          <MessageCircle size={20} />
        </button>
      )}

      {/* Chat panel */}
      {open && (
        <div className="ask-maiko-panel">
          <div className="ask-maiko-header">
            <span className="ask-maiko-title">Ask Maiko</span>
            <button className="ask-maiko-close" onClick={() => setOpen(false)}>
              <X size={14} />
            </button>
          </div>

          <div className="ask-maiko-messages">
            {messages.map((msg, i) => (
              <div key={i} className={`ask-maiko-msg ${msg.role}`}>
                {msg.role === "maiko" && <span className="ask-maiko-avatar">M</span>}
                <div className="ask-maiko-msg-text">{msg.text}</div>
              </div>
            ))}
            {loading && (
              <div className="ask-maiko-msg maiko">
                <span className="ask-maiko-avatar">M</span>
                <div className="ask-maiko-msg-text ask-maiko-typing">
                  <Loader size={12} className="spin" /> Thinking...
                </div>
              </div>
            )}
            <div ref={messagesEnd} />
          </div>

          <div className="ask-maiko-input-row">
            <textarea
              ref={inputRef}
              className="ask-maiko-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask anything..."
              rows={1}
            />
            <button
              className="ask-maiko-send"
              onClick={send}
              disabled={loading || !input.trim()}
            >
              <Send size={14} />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
