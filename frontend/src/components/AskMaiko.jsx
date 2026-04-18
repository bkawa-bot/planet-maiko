import { useState, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { MessageCircle, Send, X, Loader, ChevronDown, ChevronUp, ArrowRight, Leaf } from "lucide-react";
import "./AskMaiko.css";

// Soft cap on how many agents should already be running before we
// ask the user "sure about another?" before dispatching. Not a hard
// block — friction, not prevention. Higher than the typical single-
// thread workflow (~2 running at once) so the pause only appears
// when there's real parallel load.
const PAUSE_FIRST_THRESHOLD = 3;

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
  // Pause-first state: when a send would push past the active-agent
  // threshold, we stash the intended payload here and render a
  // confirmation step instead of dispatching immediately.
  const [pendingSend, setPendingSend] = useState(null);
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

  // Cmd/Ctrl+K anywhere in the app pops the pack open.
  useEffect(() => {
    const onOpen = () => setOpen(true);
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

  const send = async () => {
    const ask = input.trim();
    if (!ask || loading || pendingSend) return;

    const ctx = context.trim();
    const ng = nonGoals.trim();

    // Pause-first: if enough agents are already running, don't
    // dispatch blindly — show a nudge and let the user confirm.
    // Errors here fall through to dispatching (the count check is
    // best-effort; never block a send on a lookup failure).
    try {
      const tasks = await api.getTasks({ status: "in_progress" });
      const active = (tasks || []).filter((t) => t.assigned_agent_id).length;
      if (active >= PAUSE_FIRST_THRESHOLD) {
        setPendingSend({ ask, ctx, ng, active });
        return;
      }
    } catch {
      // No-op; fall through to dispatch.
    }

    dispatchNow(ask, ctx, ng);
  };

  const confirmPending = () => {
    if (!pendingSend) return;
    const { ask, ctx, ng } = pendingSend;
    setPendingSend(null);
    dispatchNow(ask, ctx, ng);
  };

  const cancelPending = () => {
    setPendingSend(null);
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
        <button className="ask-maiko-bubble" onClick={() => setOpen(true)} title="Ask the Pack (Cmd/Ctrl+K)">
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

            {pendingSend && (
              <div className="ask-pack-pause">
                <div className="ask-pack-pause-head">
                  <Leaf size={12} />
                  <span>{pendingSend.active} agents already working</span>
                </div>
                <div className="ask-pack-pause-body">
                  A lot's already in motion. Want to hold this one until your next check-in, or send it now?
                </div>
                <div className="ask-pack-pause-actions">
                  <button className="ask-pack-pause-secondary" onClick={cancelPending}>Hold it</button>
                  <button className="ask-pack-pause-primary" onClick={confirmPending}>Send anyway</button>
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


function PackTurn({ turn, onClose }) {
  if (turn.kind === "user") {
    return (
      <div className="ask-maiko-msg user">
        <div className="ask-maiko-msg-text">
          {turn.text}
          {turn.context && <div className="ask-pack-user-ctx">— {turn.context}</div>}
          {turn.nonGoals && <div className="ask-pack-user-ctx ask-pack-user-nogoals">Must not: {turn.nonGoals}</div>}
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
