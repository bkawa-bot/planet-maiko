import { useEffect, useState, useRef } from "react";
import {
  Flame, Check, X, Loader, Moon, RefreshCw, MessageSquare,
  ChevronDown, ChevronRight,
} from "@icons";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import PlaybookTab from "../PlaybookTab";
import CardAvatar from "../CardAvatar";
import "./CampfireTab.css";

// Poll interval during the gather — fast enough that speech bubbles
// feel alive as agents reply, slow enough not to hammer the server.
const GATHER_POLL_INTERVAL_MS = 4000;


export default function AgentsInsightsTab() {
  const [packState, setPackState] = useState(null);
  const [replies, setReplies] = useState({ agents: [], status: "idle", started_at: null });
  const [showPlaybook, setShowPlaybook] = useState(false);
  const [starting, setStarting] = useState(false);
  const [wrapping, setWrapping] = useState(false);
  // Per-reply Keep/Drop decisions keyed by AgentMessage.id. Default is
  // "keep" (opt-in drops) so agents the user doesn't click into stay
  // fully approved. Lives here so decisions persist across modal opens.
  const [decisions, setDecisions] = useState({});
  const [activeAgent, setActiveAgent] = useState(null);
  const pollRef = useRef(null);

  const fetchAll = async () => {
    try {
      const [state, replyData] = await Promise.all([
        api.getPackInsightsState(),
        api.getPackInsightsGatheringReplies().catch(() => ({ agents: [], status: "idle" })),
      ]);
      setPackState(state);
      setReplies(replyData);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => { fetchAll(); }, []);

  useEffect(() => {
    if (packState?.status === "gathering") {
      pollRef.current = setInterval(fetchAll, GATHER_POLL_INTERVAL_MS);
      return () => clearInterval(pollRef.current);
    }
    return undefined;
  }, [packState?.status]);

  const status = packState?.status || "idle";

  const handleStart = async () => {
    setStarting(true);
    try {
      await api.startPackInsights();
      showToast("Gathering the pack around the lantern… 🕯️", "normal");
      setDecisions({});
      await fetchAll();
    } catch (err) {
      showToast(err.message || "Couldn't start gathering", "high");
    }
    setStarting(false);
  };

  const handleWrapUp = async () => {
    setWrapping(true);
    const dropped = Object.entries(decisions)
      .filter(([, v]) => v === "drop")
      .map(([id]) => parseInt(id, 10))
      .filter((n) => Number.isInteger(n));
    try {
      const result = await api.wrapUpPackInsights(dropped);
      const kept = (result?.signals_deleted || 0) + (result?.insights_dismissed || 0);
      if (kept > 0) {
        showToast(`Dropped ${kept} · everything else merged into the pool`, "normal");
      } else {
        showToast("Wrapped up — everything the pack shared stays in the pool", "normal");
      }
      setDecisions({});
      setActiveAgent(null);
      await fetchAll();
    } catch (err) {
      showToast(err.message || "Couldn't wrap up", "high");
    }
    setWrapping(false);
  };

  const handleReset = async () => {
    await api.resetPackInsights();
    setDecisions({});
    setActiveAgent(null);
    fetchAll();
  };

  const setReplyDecision = (replyId, decision) => {
    setDecisions((prev) => ({ ...prev, [replyId]: decision }));
  };

  return (
    <div className="campfire-tab">
      {status === "idle" && (
        <IdleHero onStart={handleStart} starting={starting} />
      )}

      {status === "gathering" && (
        <CampfireScene
          agents={replies.agents}
          decisions={decisions}
          onOpenAgent={setActiveAgent}
          onWrapUp={handleWrapUp}
          onReset={handleReset}
          wrapping={wrapping}
        />
      )}

      {/* Legacy state transitions kept in case an external caller
          (CLI, API) walks the old pipeline. The new in-app flow goes
          straight from gathering → idle via /pack-insights/wrap-up. */}
      {(status === "reviewing" || status === "synthesized" || status === "finalized") && (
        <LegacyPanel packState={packState} onReset={handleReset} />
      )}

      {activeAgent && (
        <AgentGatherModal
          agent={activeAgent}
          decisions={decisions}
          onDecision={setReplyDecision}
          onClose={() => setActiveAgent(null)}
        />
      )}

      <div className="campfire-library">
        <button
          className="campfire-library-toggle"
          onClick={() => setShowPlaybook((v) => !v)}
        >
          {showPlaybook ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          <span>Pack Insights library</span>
        </button>
        {showPlaybook && (
          <div className="campfire-library-body">
            <PlaybookTab />
          </div>
        )}
      </div>
    </div>
  );
}


function IdleHero({ onStart, starting }) {
  return (
    <div className="campfire-idle">
      <div className="campfire-fire campfire-fire-dim">🕯️</div>
      <h3>Gather the pack around the fire</h3>
      <p>
        Maiko will message every active agent and ask what they noticed today —
        coding rules (<em>feedback</em>) and tribal knowledge (<em>insights</em>).
        Each agent will also share a one-sentence summary of their day.
        You click an agent to review what they said and approve what sticks.
      </p>
      <button
        className="btn btn-primary campfire-start"
        onClick={onStart}
        disabled={starting}
      >
        {starting ? <Loader size={14} className="spin" /> : <Flame size={14} />}
        {starting ? " Lighting the fire…" : " Start the gathering"}
      </button>
    </div>
  );
}


function CampfireScene({ agents, decisions, onOpenAgent, onWrapUp, onReset, wrapping }) {
  const sharedCount = agents.filter((a) => a.state === "shared").length;
  const total = agents.length;

  return (
    <div className="campfire-scene">
      <div className="campfire-fire campfire-fire-live">🕯️</div>

      <div className="campfire-ring">
        {agents.length === 0 ? (
          <div className="campfire-empty">
            The pack hasn't arrived yet… Maiko is waking them up.
          </div>
        ) : (
          agents.map((a) => (
            <AgentAtFire
              key={a.agent_id}
              agent={a}
              decisions={decisions}
              onOpen={() => onOpenAgent(a)}
            />
          ))
        )}
      </div>

      <div className="campfire-progress">
        <span className="campfire-progress-text">
          {total === 0
            ? "Waiting on the pack…"
            : `${sharedCount} of ${total} shared`}
        </span>
        <div className="campfire-progress-actions">
          <button className="btn btn-sm btn-ghost" onClick={onReset}>
            <X size={10} /> Cancel
          </button>
          <button
            className="btn btn-sm btn-primary"
            onClick={onWrapUp}
            disabled={wrapping}
          >
            {wrapping ? <Loader size={10} className="spin" /> : <Check size={10} />}
            {wrapping ? " Wrapping up…" : " Wrap up"}
          </button>
        </div>
      </div>
    </div>
  );
}


function AgentAtFire({ agent, decisions, onOpen }) {
  const replyCount = agent.replies?.length || 0;
  const droppedCount = (agent.replies || []).filter((r) => decisions[r.id] === "drop").length;

  // Fallback summary if the agent didn't send a summary reply yet: a
  // dry count so the bubble isn't empty while you wait for them.
  const fallbackCount = (() => {
    if (!replyCount) return null;
    const fb = (agent.replies || []).filter((r) => r.type === "feedback").length;
    const ins = (agent.replies || []).filter((r) => r.type === "insight").length;
    const parts = [];
    if (fb) parts.push(`${fb} feedback`);
    if (ins) parts.push(`${ins} insight${ins === 1 ? "" : "s"}`);
    return `Shared ${parts.join(" · ")}`;
  })();

  const bubbleText = agent.summary || fallbackCount;

  return (
    <div
      className={`campfire-agent campfire-agent-${agent.state} ${onOpen ? "clickable" : ""}`}
      title={agent.task_title}
      onClick={agent.state === "shared" ? onOpen : undefined}
      role={agent.state === "shared" ? "button" : undefined}
    >
      <div className="campfire-bubbles">
        {agent.state === "waiting" && (
          <div className="campfire-bubble campfire-bubble-waiting">
            <span className="thinking-dots">●●●</span>
          </div>
        )}
        {agent.state === "quiet" && (
          <div className="campfire-bubble campfire-bubble-quiet">
            <Moon size={10} /> quiet tonight
          </div>
        )}
        {agent.state === "shared" && bubbleText && (
          <div className="campfire-bubble campfire-bubble-summary">
            <span className="campfire-bubble-text">{bubbleText}</span>
            <span className="campfire-bubble-meta">
              <MessageSquare size={9} />
              {replyCount}
              {droppedCount > 0 && (
                <span className="campfire-bubble-dropped" title={`${droppedCount} dropped`}>
                  · −{droppedCount}
                </span>
              )}
            </span>
          </div>
        )}
      </div>
      <div className="campfire-agent-avatar">
        <CardAvatar agent={agent} size="lg" />
      </div>
      <div className="campfire-agent-name">{agent.display_name}</div>
    </div>
  );
}


function AgentGatherModal({ agent, decisions, onDecision, onClose }) {
  const feedback = (agent.replies || []).filter((r) => r.type === "feedback");
  const insights = (agent.replies || []).filter((r) => r.type === "insight");

  const decisionOf = (id) => decisions[id] || "keep";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="campfire-modal" onClick={(e) => e.stopPropagation()}>
        <div className="campfire-modal-header">
          <div className="campfire-modal-avatar">
            <CardAvatar agent={agent} size={36} />
          </div>
          <div className="campfire-modal-identity">
            <div className="campfire-modal-name">{agent.display_name}</div>
            {agent.task_title && (
              <div className="campfire-modal-task">{agent.task_title}</div>
            )}
          </div>
          <button className="btn-ghost" onClick={onClose} title="Close">
            <X size={14} />
          </button>
        </div>

        {agent.summary && (
          <div className="campfire-modal-summary">
            <span className="campfire-modal-summary-quote">"{agent.summary}"</span>
          </div>
        )}

        {feedback.length > 0 && (
          <div className="campfire-modal-section">
            <div className="campfire-modal-section-header campfire-modal-section-feedback">
              Feedback <span className="campfire-modal-section-count">{feedback.length}</span>
            </div>
            <div className="campfire-modal-items">
              {feedback.map((r) => (
                <ReplyRow
                  key={r.id}
                  reply={r}
                  decision={decisionOf(r.id)}
                  onDecision={(d) => onDecision(r.id, d)}
                />
              ))}
            </div>
          </div>
        )}

        {insights.length > 0 && (
          <div className="campfire-modal-section">
            <div className="campfire-modal-section-header campfire-modal-section-insight">
              Insights <span className="campfire-modal-section-count">{insights.length}</span>
            </div>
            <div className="campfire-modal-items">
              {insights.map((r) => (
                <ReplyRow
                  key={r.id}
                  reply={r}
                  decision={decisionOf(r.id)}
                  onDecision={(d) => onDecision(r.id, d)}
                />
              ))}
            </div>
          </div>
        )}

        {feedback.length === 0 && insights.length === 0 && (
          <div className="campfire-modal-empty">
            {agent.display_name} hasn't shared anything yet.
          </div>
        )}

        <div className="campfire-modal-footer">
          <span className="campfire-modal-hint">
            Decisions are saved when you Wrap up the gathering.
          </span>
          <button className="btn btn-sm btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}


function ReplyRow({ reply, decision, onDecision }) {
  return (
    <div className={`campfire-reply campfire-reply-${decision}`}>
      <div className="campfire-reply-text">{reply.content}</div>
      <div className="campfire-reply-actions">
        <button
          className={`btn btn-xs ${decision === "keep" ? "btn-primary" : ""}`}
          onClick={() => onDecision("keep")}
          title="Keep this — it'll merge into the pool on Wrap up"
        >
          <Check size={10} /> Keep
        </button>
        <button
          className={`btn btn-xs ${decision === "drop" ? "btn-danger" : ""}`}
          onClick={() => onDecision("drop")}
          title="Drop this — Maiko will remove it on Wrap up"
        >
          <X size={10} /> Drop
        </button>
      </div>
    </div>
  );
}


function LegacyPanel({ packState, onReset }) {
  return (
    <div className="campfire-panel">
      <div className="campfire-panel-header">
        <Flame size={14} /> Gathering in legacy state: {packState?.status}
      </div>
      <p style={{ fontSize: 12, color: "var(--text-muted)" }}>
        The ritual advanced via an older endpoint. Hit Reset to return to idle
        and start a fresh gathering.
      </p>
      <div className="campfire-panel-footer">
        <button className="btn" onClick={onReset}>
          <RefreshCw size={10} /> Reset
        </button>
      </div>
    </div>
  );
}
