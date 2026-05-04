import { useState, useRef, useEffect } from "react";
import { api } from "../api/client";
import { Send, X, Loader, ChevronDown, ChevronUp } from "lucide-react";
import PackTurn from "./PackTurn";
import "./AskMaiko.css";

// Persist dispatch history for the tab's lifetime — closing the panel
// or nav'ing between pages shouldn't erase "what did the pack say 20
// seconds ago". sessionStorage (not localStorage) so a new tab starts
// fresh.
const TURNS_KEY = "ask-pack-turns:v1";

function loadTurns() {
  try {
    const raw = sessionStorage.getItem(TURNS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export default function AskMaiko() {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState(loadTurns);
  const [input, setInput] = useState("");
  const [context, setContext] = useState("");
  const [nonGoals, setNonGoals] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [loading, setLoading] = useState(false);
  const messagesEnd = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    try {
      sessionStorage.setItem(TURNS_KEY, JSON.stringify(turns.slice(-20)));
    } catch {
      /* quota exceeded or private mode — drop silently, the ephemeral
         state in memory is still correct. */
    }
  }, [turns]);

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, loading]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Cmd/Ctrl+K anywhere in the app pops the pack open. Embedded
  // callers (the "Ask the pack" sidebar widget on Home, via
  // PackAskBox) can hand off a prefilled query via event.detail.text
  // — lets the inline box feel contextual without duplicating all
  // the panel logic here.
  useEffect(() => {
    const onOpen = (e) => {
      setOpen(true);
      const detail = e && e.detail;
      if (detail && detail.text) {
        setInput(detail.text);
        if (detail.context) setContext(detail.context);
        if (detail.nonGoals) setNonGoals(detail.nonGoals);
      }
    };
    window.addEventListener("open-ask-pack", onOpen);
    return () => window.removeEventListener("open-ask-pack", onOpen);
  }, []);

  const dispatchNow = async (ask, ctx, ng) => {
    setTurns((prev) => [...prev, { kind: "user", text: ask, context: ctx, nonGoals: ng }]);
    setInput("");
    setContext("");
    setNonGoals("");
    setShowDetails(false);
    setLoading(true);

    try {
      const res = await api.dispatchPack(ask, ctx, ng);
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

  const send = () => {
    const ask = input.trim();
    if (!ask || loading) return;
    dispatchNow(ask, context.trim(), nonGoals.trim());
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  // The floating bubble is gone — the "Ask the pack" sidebar widget
  // on Home (PackAskBox) is the primary entry point. Cmd/Ctrl+K
  // opens this panel from anywhere without a prefilled query.
  return (
    <>
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
            {showDetails && (
              <>
                <textarea
                  className="ask-pack-context"
                  value={context}
                  onChange={(e) => setContext(e.target.value)}
                  placeholder="Context — URL, file path, deadline, anything that helps…"
                  rows={2}
                />
                <textarea
                  className="ask-pack-context ask-pack-must-not"
                  value={nonGoals}
                  onChange={(e) => setNonGoals(e.target.value)}
                  placeholder="Must not — boundaries for the agent (e.g. 'don't touch the billing code', 'no new deps')"
                  rows={2}
                />
              </>
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
              onClick={() => setShowDetails((s) => !s)}
              type="button"
            >
              {showDetails ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
              {showDetails ? "Hide details" : "Add context / boundaries"}
            </button>
          </div>
        </div>
      )}
    </>
  );
}


