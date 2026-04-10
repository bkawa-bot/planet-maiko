import { useEffect, useState } from "react";
import { Flame, Play, Sparkles, Check } from "lucide-react";
import { api } from "../../api/client";
import { showToast } from "../Toast";

const PACK_CATEGORIES = ["domain_knowledge", "pattern", "gotcha", "team"];
const PIPELINE_STEPS = ["idle", "gathering", "reviewing", "synthesized", "finalized"];

/**
 * Pack Insights tab — the collaborative learnings ritual.
 *
 * Owns its own state machine (idle → gathering → reviewing → synthesized
 * → finalized) and all the API calls. Drop into Agents.jsx as
 * <AgentsInsightsTab /> with no props needed.
 */
export default function AgentsInsightsTab() {
  const [packState, setPackState] = useState(null);
  const [manualText, setManualText] = useState("");
  const [manualCategory, setManualCategory] = useState("domain_knowledge");

  const fetchPack = async () => {
    try {
      setPackState(await api.getPackInsightsState());
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => { fetchPack(); }, []);

  const handleStart = async () => {
    await api.startPackInsights();
    showToast("Gathering the pack...", "normal");
    fetchPack();
  };
  const handleCollect = async () => {
    await api.collectPackInsights();
    showToast("Learnings collected!", "normal");
    fetchPack();
  };
  const handleSynthesize = async () => {
    await api.synthesizePackInsights();
    showToast("Synthesizing...", "normal");
    fetchPack();
  };
  const handleFinalize = async () => {
    await api.finalizePackInsights({});
    showToast("Merged into knowledge pool!", "normal");
    fetchPack();
  };
  const handleAdd = async () => {
    if (!manualText.trim()) return;
    await api.addPackInsightsLearning(manualText, manualCategory);
    setManualText("");
    fetchPack();
  };

  const status = packState?.status || "idle";
  const currentIdx = PIPELINE_STEPS.indexOf(status);

  return (
    <div className="gathering-panel">
      <div className="pipeline">
        {PIPELINE_STEPS.map((step, idx) => (
          <div
            key={step}
            className={`pipe-step ${idx === currentIdx ? "current" : ""} ${idx < currentIdx ? "done" : ""}`}
          >
            {step}
          </div>
        ))}
      </div>

      {status === "idle" && (
        <div className="panel-center">
          <p>Start a session to collect learnings from the pack</p>
          <button className="btn btn-primary" onClick={handleStart}>
            <Flame size={12} /> Start Pack Insights
          </button>
        </div>
      )}

      {status === "gathering" && (
        <div className="panel-center">
          <p>Gathering signal sent. Click collect when agents have reported.</p>
          <button className="btn btn-primary" onClick={handleCollect}>
            <Play size={12} /> Collect from Pack
          </button>
        </div>
      )}

      {status === "reviewing" && (
        <>
          <div className="add-learning-row">
            <input
              value={manualText}
              onChange={(e) => setManualText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
              placeholder="What did you learn today?"
            />
            <select value={manualCategory} onChange={(e) => setManualCategory(e.target.value)}>
              {PACK_CATEGORIES.map((c) => (
                <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
              ))}
            </select>
            <button className="btn" onClick={handleAdd}><Check size={12} /></button>
          </div>
          <div className="learning-items">
            {(packState.collected || []).map((item, i) => (
              <div key={i} className="learning-item">
                <div className="learning-text">{item.text}</div>
                <div className="learning-tags">
                  <span className="tag">{item.category?.replace(/_/g, " ")}</span>
                  {item.source_agent && <span className="tag">{item.source_agent}</span>}
                </div>
              </div>
            ))}
          </div>
          <div className="panel-footer">
            <span className="count-text">{packState.collected?.length || 0} learnings</span>
            <button className="btn btn-primary" onClick={handleSynthesize}>
              <Sparkles size={12} /> Synthesize
            </button>
          </div>
        </>
      )}

      {status === "synthesized" && packState.synthesis && (
        <>
          <div className="synthesis-report card synthesis-report-pink">
            <div className="synthesis-header">Maiko's Synthesis</div>
            <ul className="synthesis-bullets">
              <li>{packState.synthesis.duplicates_merged} duplicates merged</li>
              <li>{packState.synthesis.already_known?.length || 0} already known</li>
              <li>{packState.synthesis.unique_learnings?.length || 0} new learnings</li>
              <li>{packState.synthesis.proposed_rules?.length || 0} proposed rules</li>
            </ul>
          </div>
          {packState.synthesis.proposed_rules?.map((r, i) => (
            <div key={i} className="card proposed-rule-card">
              <span className="tag">{r.category}</span>
              <div className="proposed-rule-text">{r.text}</div>
            </div>
          ))}
          <div className="panel-footer panel-footer-spaced">
            <button className="btn btn-primary" onClick={handleFinalize}>
              <Check size={12} /> Finalize & Merge
            </button>
          </div>
        </>
      )}

      {status === "finalized" && (
        <div className="panel-center">
          <Check size={24} className="finalize-check-icon" />
          <p>Learnings have been merged into the global knowledge pool.</p>
        </div>
      )}
    </div>
  );
}
