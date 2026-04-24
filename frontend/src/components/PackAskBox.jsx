import { useEffect, useState } from "react";
import { Send, Loader, Trash2, ChevronDown, ChevronUp } from "lucide-react";
import { api } from "../api/client";
import PackTurn from "./PackTurn";
import "./PackAskBox.css";

/**
 * Inline "Ask the pack" widget. Lives at the top of PackStatusPane
 * on Home. Dispatches directly to the pack and renders the answer
 * right here — no corner popup, no context-switch. Shares styling
 * with the AskMaiko floating panel (which handles Cmd/Ctrl+K from
 * other pages) via the ask-maiko-* / ask-pack-* classes and the
 * PackTurn component.
 *
 * Pause-first gating (too-many-active) kicks in when the pack is
 * already chewing on a lot — a "sure?" step, not a hard block.
 */

const PAUSE_FIRST_THRESHOLD = 3;


export default function PackAskBox() {
  const [text, setText] = useState("");
  const [context, setContext] = useState("");
  const [nonGoals, setNonGoals] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [turns, setTurns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadSeconds, setLoadSeconds] = useState(0);
  const [pendingSend, setPendingSend] = useState(null);

  // Tick up while dispatch is in flight so the spinner can show a
  // "still thinking" hint past the point where instant responses stop.
  // Router has a 45s ceiling; anything over ~8s means the LLM is
  // chewing on the request, not that we broke.
  useEffect(() => {
    if (!loading) {
      setLoadSeconds(0);
      return undefined;
    }
    const id = setInterval(() => setLoadSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [loading]);

  const dispatchNow = async (ask, ctx, ng) => {
    setTurns((prev) => [...prev, { kind: "user", text: ask, context: ctx, nonGoals: ng }]);
    setText("");
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

  const submit = async () => {
    const ask = text.trim();
    if (!ask || loading || pendingSend) return;
    const ctx = context.trim();
    const ng = nonGoals.trim();

    // Pause-first: surface too-many-active as a "sure?" step rather
    // than silently piling another agent on top.
    try {
      const tasks = await api.getTasks({ status: "in_progress" }).catch(() => []);
      const active = (tasks || []).filter((t) => t.assigned_agent_id).length;
      if (active >= PAUSE_FIRST_THRESHOLD) {
        setPendingSend({ ask, ctx, ng, active, reason: "load" });
        return;
      }
    } catch {
      /* lookups are best-effort — never block dispatch on a GET fail. */
    }
    dispatchNow(ask, ctx, ng);
  };

  const confirmPending = () => {
    if (!pendingSend) return;
    const { ask, ctx, ng } = pendingSend;
    setPendingSend(null);
    dispatchNow(ask, ctx, ng);
  };
  const cancelPending = () => setPendingSend(null);

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const clearThread = () => {
    setTurns([]);
    setPendingSend(null);
  };

  return (
    <div className="pack-ask-wrap">
      <div className="pack-ask-box">
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder="Ask the pack…"
          className="pack-ask-input"
          disabled={loading}
        />
        <button
          type="button"
          className="pack-ask-send"
          onClick={submit}
          disabled={!text.trim() || loading || pendingSend}
          title="Hand off to an agent"
        >
          {loading ? <Loader size={12} className="spin" /> : <Send size={12} />}
        </button>
      </div>

      <button
        className="pack-ask-details-toggle"
        onClick={() => setShowDetails((s) => !s)}
        type="button"
      >
        {showDetails ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
        {showDetails ? "Hide details" : "Add context / boundaries"}
      </button>

      {showDetails && (
        <div className="pack-ask-details">
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
            placeholder="Must not — boundaries for the agent (e.g. 'don't touch billing', 'no new deps')"
            rows={2}
          />
        </div>
      )}

      {(turns.length > 0 || loading || pendingSend) && (
        <div className="pack-ask-thread">
          {turns.map((turn, i) => (
            <PackTurn key={i} turn={turn} />
          ))}

          {loading && (
            <div className="ask-maiko-msg maiko">
              <span className="ask-maiko-avatar">M</span>
              <div className="ask-maiko-msg-text ask-maiko-typing">
                <Loader size={12} className="spin" />
                {loadSeconds < 8
                  ? " Finding the right agent…"
                  : loadSeconds < 30
                    ? " Still thinking — router runs an LLM, usually settles in under 30s."
                    : " Almost there — router has a 45s ceiling before it gives up."}
              </div>
            </div>
          )}

          {pendingSend && (
            <div className="ask-pack-pause">
              <div className="ask-pack-pause-head">
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

          {turns.length > 0 && !loading && (
            <button className="pack-ask-clear" onClick={clearThread}>
              <Trash2 size={10} /> Clear thread
            </button>
          )}
        </div>
      )}
    </div>
  );
}
