import { useEffect, useState, useRef } from "react";
import {
  Flame, Check, X, Plus, Sparkles, ChevronDown, ChevronRight, Loader, Moon, RefreshCw,
} from "lucide-react";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import PlaybookTab from "../PlaybookTab";
import "./CampfireTab.css";

const PACK_CATEGORIES = ["domain_knowledge", "pattern", "gotcha", "team"];

// Mirror of Home.jsx AVATAR_EMOJI + src/planet_maiko/agents/signature.py —
// keep all three in sync.
const AVATAR_EMOJI = {
  shiba: "🐕", corgi: "🐶", husky: "🐺", poodle: "🐩", golden: "🦮",
  beagle: "🐕‍🦺", dalmatian: "🐾", samoyed: "☁️", akita: "🐕", pomeranian: "🧸",
  calico_cat: "🐱", tabby_cat: "🐈", black_cat: "🐈‍⬛",
  bunny: "🐰", hamster: "🐹", fox: "🦊",
};

// Poll interval during the gather — fast enough that speech bubbles
// feel alive as agents reply, slow enough not to hammer the server.
const GATHER_POLL_INTERVAL_MS = 4000;


export default function AgentsInsightsTab() {
  const [packState, setPackState] = useState(null);
  const [replies, setReplies] = useState({ agents: [], status: "idle", started_at: null });
  const [manualText, setManualText] = useState("");
  const [manualCategory, setManualCategory] = useState("domain_knowledge");
  const [showPlaybook, setShowPlaybook] = useState(false);
  const [starting, setStarting] = useState(false);
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

  // Poll only while gathering. Idle / reviewing / synthesized states
  // don't change without a user action, so polling them would be
  // background noise.
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
      showToast("Gathering the pack around the fire… 🔥", "normal");
      await fetchAll();
    } catch (err) {
      showToast(err.message || "Couldn't start gathering", "high");
    }
    setStarting(false);
  };

  const handleWrap = async () => {
    await api.collectPackInsights();
    showToast("Collected what the pack shared", "normal");
    fetchAll();
  };

  const handleSynthesize = async () => {
    await api.synthesizePackInsights();
    showToast("Maiko is looking for patterns…", "normal");
    fetchAll();
  };

  const handleFinalize = async () => {
    const result = await api.finalizePackInsights({});
    const kept = result?.kept || 0;
    const rules = result?.rules_created || 0;
    showToast(`Merged ${kept} into the pool · ${rules} proposed rules`, "normal");
    fetchAll();
  };

  const handleReset = async () => {
    await api.resetPackInsights();
    fetchAll();
  };

  const handleAddManual = async () => {
    if (!manualText.trim()) return;
    await api.addPackInsightsLearning(manualText, manualCategory);
    setManualText("");
    fetchAll();
  };

  return (
    <div className="campfire-tab">
      {status === "idle" && (
        <IdleHero onStart={handleStart} starting={starting} />
      )}

      {status === "gathering" && (
        <CampfireScene
          agents={replies.agents}
          onWrap={handleWrap}
          onReset={handleReset}
        />
      )}

      {status === "reviewing" && (
        <ReviewingPanel
          packState={packState}
          manualText={manualText}
          setManualText={setManualText}
          manualCategory={manualCategory}
          setManualCategory={setManualCategory}
          onAddManual={handleAddManual}
          onSynthesize={handleSynthesize}
          onReset={handleReset}
        />
      )}

      {status === "synthesized" && (
        <SynthesizedPanel
          packState={packState}
          onFinalize={handleFinalize}
          onReset={handleReset}
        />
      )}

      {status === "finalized" && (
        <FinalizedPanel onReset={handleReset} />
      )}

      {/* Always-visible library of active insights (the Playbook). Lives
          here so agents + their captured context stay in one place. */}
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
      <div className="campfire-fire campfire-fire-dim">🔥</div>
      <h3>Gather the pack around the fire</h3>
      <p>
        Maiko will message every active agent and ask what they noticed today —
        coding rules that should apply to future work (<em>feedback</em>) and
        tribal knowledge future agents should inherit (<em>insights</em>).
        You approve what sticks.
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


function CampfireScene({ agents, onWrap, onReset }) {
  const sharedCount = agents.filter((a) => a.state === "shared").length;
  const total = agents.length;

  return (
    <div className="campfire-scene">
      <div className="campfire-fire campfire-fire-live">🔥</div>

      <div className="campfire-ring">
        {agents.length === 0 ? (
          <div className="campfire-empty">
            The pack hasn't arrived yet… Maiko is waking them up.
          </div>
        ) : (
          agents.map((a) => <AgentAtFire key={a.agent_id} agent={a} />)
        )}
      </div>

      <div className="campfire-progress">
        <span className="campfire-progress-text">
          {total === 0
            ? "Waiting on the pack…"
            : `${sharedCount} of ${total} shared`}
        </span>
        <div className="campfire-progress-actions">
          <button className="btn btn-sm btn-ghost" onClick={onReset} title="Cancel the gathering">
            <X size={10} /> Cancel
          </button>
          <button className="btn btn-sm btn-primary" onClick={onWrap}>
            <Check size={10} /> Wrap up
          </button>
        </div>
      </div>
    </div>
  );
}


function AgentAtFire({ agent }) {
  const emoji = AVATAR_EMOJI[agent.avatar] || "🐾";

  return (
    <div className={`campfire-agent campfire-agent-${agent.state}`} title={agent.task_title}>
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
        {agent.replies.map((r, i) => (
          <div key={i} className={`campfire-bubble campfire-bubble-${r.type}`}>
            <span className="campfire-bubble-type">{r.type}</span>
            <span className="campfire-bubble-text">{r.content}</span>
          </div>
        ))}
      </div>
      <div className="campfire-agent-avatar">{emoji}</div>
      <div className="campfire-agent-name">{agent.display_name}</div>
    </div>
  );
}


function ReviewingPanel({
  packState, manualText, setManualText, manualCategory, setManualCategory,
  onAddManual, onSynthesize, onReset,
}) {
  const collected = packState?.collected || [];
  return (
    <div className="campfire-panel">
      <div className="campfire-panel-header">
        <Flame size={14} /> What the pack shared
        <span className="campfire-panel-count">{collected.length}</span>
      </div>
      <div className="campfire-manual-add">
        <input
          value={manualText}
          onChange={(e) => setManualText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onAddManual()}
          placeholder="Add your own note…"
        />
        <select value={manualCategory} onChange={(e) => setManualCategory(e.target.value)}>
          {PACK_CATEGORIES.map((c) => (
            <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
          ))}
        </select>
        <button className="btn btn-sm" onClick={onAddManual}>
          <Plus size={10} />
        </button>
      </div>
      <div className="campfire-collected">
        {collected.length === 0 ? (
          <div className="campfire-empty-small">No replies collected yet.</div>
        ) : (
          collected.map((item, i) => (
            <div key={i} className="campfire-collected-item">
              <div className="campfire-collected-text">{item.text}</div>
              <div className="campfire-collected-meta">
                <span className="tag">{item.category?.replace(/_/g, " ")}</span>
                {item.source_agent && <span className="tag">{item.source_agent}</span>}
              </div>
            </div>
          ))
        )}
      </div>
      <div className="campfire-panel-footer">
        <button className="btn btn-ghost" onClick={onReset}>
          <X size={10} /> Cancel
        </button>
        <button className="btn btn-primary" onClick={onSynthesize}>
          <Sparkles size={12} /> Synthesize
        </button>
      </div>
    </div>
  );
}


function SynthesizedPanel({ packState, onFinalize, onReset }) {
  const synth = packState?.synthesis || {};
  return (
    <div className="campfire-panel">
      <div className="campfire-panel-header">
        <Sparkles size={14} /> Maiko's synthesis
      </div>
      <ul className="campfire-synth-bullets">
        <li>{synth.duplicates_merged || 0} duplicates merged</li>
        <li>{(synth.already_known || []).length} already in the pool</li>
        <li>{(synth.unique_learnings || []).length} fresh learnings</li>
        <li>{(synth.proposed_rules || []).length} proposed rules</li>
      </ul>
      {(synth.proposed_rules || []).length > 0 && (
        <div className="campfire-proposed-rules">
          {synth.proposed_rules.map((r, i) => (
            <div key={i} className="campfire-proposed-rule">
              <span className="tag">{r.category}</span>
              <span>{r.text}</span>
            </div>
          ))}
        </div>
      )}
      <div className="campfire-panel-footer">
        <button className="btn btn-ghost" onClick={onReset}>
          <X size={10} /> Reset
        </button>
        <button className="btn btn-primary" onClick={onFinalize}>
          <Check size={12} /> Finalize &amp; merge
        </button>
      </div>
    </div>
  );
}


function FinalizedPanel({ onReset }) {
  return (
    <div className="campfire-panel campfire-panel-finalized">
      <Check size={28} className="campfire-finalized-check" />
      <h3>Merged into the pool</h3>
      <p>Learnings flowed into the knowledge pool. Insights went to the library below.</p>
      <button className="btn" onClick={onReset}>
        <RefreshCw size={10} /> Start a new gathering
      </button>
    </div>
  );
}
