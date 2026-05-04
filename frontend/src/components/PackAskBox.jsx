import { useEffect, useState } from "react";
import { Send, Loader, Trash2, ChevronDown, ChevronUp } from "lucide-react";
import { api } from "../api/client";
import PackTurn from "./PackTurn";
import PlanetSpinner from "./PlanetSpinner";
import "./PackAskBox.css";

/**
 * Inline "Ask the pack" widget. Lives in the Home sidebar.
 * Dispatches directly to the pack and renders the answer right
 * here — no corner popup, no context-switch. Shares styling with
 * the AskMaiko floating panel (which handles Cmd/Ctrl+K from other
 * pages) via the ask-maiko-* / ask-pack-* classes and the PackTurn
 * component.
 */

export default function PackAskBox() {
  const [text, setText] = useState("");
  const [context, setContext] = useState("");
  const [nonGoals, setNonGoals] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [turns, setTurns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadSeconds, setLoadSeconds] = useState(0);

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

  const submit = () => {
    const ask = text.trim();
    if (!ask || loading) return;
    dispatchNow(ask, context.trim(), nonGoals.trim());
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const clearThread = () => {
    setTurns([]);
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
          disabled={!text.trim() || loading}
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

      {(turns.length > 0 || loading) && (
        <div className="pack-ask-thread">
          {turns.map((turn, i) => (
            <PackTurn key={i} turn={turn} />
          ))}

          {loading && (
            <div className="ask-maiko-msg maiko">
              <span className="ask-maiko-avatar">M</span>
              <div className="ask-maiko-msg-text ask-maiko-typing">
                <PlanetSpinner size={14} />
                {loadSeconds < 8
                  ? " Finding the right agent…"
                  : loadSeconds < 30
                    ? " Still thinking — router runs an LLM, usually settles in under 30s."
                    : " Almost there — router has a 45s ceiling before it gives up."}
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
