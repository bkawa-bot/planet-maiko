import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { Flame, Play, Sparkles, Check, Plus, X, Clock } from "lucide-react";
import "./Gathering.css";

const CATEGORIES = ["domain_knowledge", "pattern", "gotcha", "team"];

export default function Gathering() {
  const [state, setState] = useState(null);
  const [manualText, setManualText] = useState("");
  const [manualCategory, setManualCategory] = useState("domain_knowledge");
  const [loading, setLoading] = useState(true);

  const fetchState = async () => {
    try { setState(await api.getEodState()); } catch (err) { console.error(err); }
    setLoading(false);
  };

  useEffect(() => { fetchState(); }, []);

  const handleStart = async () => { await api.startEod(); showToast("Gathering the pack around the campfire... 🔥", "normal"); fetchState(); };
  const handleCollect = async () => { await api.collectEod(); showToast("Learnings collected from the pack!", "normal"); fetchState(); };
  const handleSynthesize = async () => { await api.synthesizeEod(); showToast("Maiko is synthesizing... ✨", "normal"); fetchState(); };
  const handleFinalize = async () => { await api.finalizeEod({}); showToast("Learnings merged into the knowledge pool! 🎉", "normal"); fetchState(); };

  const handleAdd = async () => {
    if (!manualText.trim()) return;
    await fetch("http://localhost:8420/api/eod/add", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: manualText, category: manualCategory }),
    });
    setManualText("");
    fetchState();
  };

  if (loading) return <p className="page-empty">Loading...</p>;
  const status = state?.status || "idle";

  return (
    <div className="gathering-page">
      <div className="gathering-layout">
        {/* Campfire scene */}
        <div className="campfire-scene">
          <svg viewBox="0 0 200 150" className="campfire-svg">
            {/* Hills */}
            <ellipse cx="100" cy="150" rx="130" ry="50" fill="#2a4a3a" />
            <ellipse cx="100" cy="150" rx="100" ry="35" fill="#1a3a2a" />
            {/* Campfire */}
            <circle cx="100" cy="110" r="20" fill="rgba(255,150,50,0.1)" />
            <circle cx="100" cy="110" r="12" fill="rgba(255,150,50,0.2)" />
            <text x="100" y="115" textAnchor="middle" fontSize="16">🔥</text>
            {/* Maiko */}
            <text x="70" y="100" fontSize="14">🐕</text>
            <text x="70" y="125" textAnchor="middle" fontSize="6" fill="#F891B4">Maiko</text>
            {/* Pack members (if agents reported) */}
            {(state?.agents_reported || []).map((agent, i) => {
              const angle = ((i + 1) / ((state?.agents_reported?.length || 1) + 1)) * Math.PI;
              const x = 100 + Math.cos(angle) * 40;
              const y = 110 - Math.sin(angle) * 25;
              return (
                <g key={agent}>
                  <text x={x} y={y} fontSize="12" textAnchor="middle">🤖</text>
                  <text x={x} y={y + 12} textAnchor="middle" fontSize="5" fill="var(--text-muted)">{agent.slice(0, 8)}</text>
                </g>
              );
            })}
          </svg>
        </div>

        {/* Panel */}
        <div className="gathering-panel">
          <div className="gathering-header">
            <Flame size={18} className="fire-icon" />
            <h2>Evening Roundup</h2>
          </div>

          {/* Pipeline */}
          <div className="pipeline">
            {["idle", "gathering", "reviewing", "synthesized", "finalized"].map((step) => {
              const steps = ["idle", "gathering", "reviewing", "synthesized", "finalized"];
              const idx = steps.indexOf(step);
              const currentIdx = steps.indexOf(status);
              return (
                <div key={step} className={`pipe-step ${idx === currentIdx ? "current" : ""} ${idx < currentIdx ? "done" : ""}`}>
                  {step}
                </div>
              );
            })}
          </div>

          {status === "idle" && (
            <div className="panel-center">
              <p>Start the evening ritual to collect learnings from the pack</p>
              <button className="btn btn-primary" onClick={handleStart}>
                <Flame size={12} /> Start Evening Roundup
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
              {/* Add learning form */}
              <div className="add-learning-row">
                <input
                  value={manualText}
                  onChange={(e) => setManualText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAdd()}
                  placeholder="What did you learn today?"
                />
                <select value={manualCategory} onChange={(e) => setManualCategory(e.target.value)}>
                  {CATEGORIES.map((c) => (
                    <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
                  ))}
                </select>
                <button className="btn" onClick={handleAdd}><Check size={12} /></button>
              </div>

              {/* Learning list */}
              <div className="learning-items">
                {(state.collected || []).map((item, i) => (
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
                <span className="count-text">{state.collected?.length || 0} learnings</span>
                <button className="btn btn-primary" onClick={handleSynthesize}>
                  <Sparkles size={12} /> Synthesize
                </button>
              </div>
            </>
          )}

          {status === "synthesized" && state.synthesis && (
            <>
              <div className="synthesis-report card" style={{ borderLeft: "3px solid var(--pink)" }}>
                <div className="synthesis-header">Maiko's Synthesis</div>
                <ul className="synthesis-bullets">
                  <li>{state.synthesis.duplicates_merged} duplicates merged</li>
                  <li>{state.synthesis.already_known?.length || 0} already known</li>
                  <li>{state.synthesis.unique_learnings?.length || 0} new learnings</li>
                  <li>{state.synthesis.proposed_rules?.length || 0} proposed rules</li>
                </ul>
              </div>

              {state.synthesis.proposed_rules?.map((r, i) => (
                <div key={i} className="card" style={{ borderLeft: "3px solid var(--blue)", marginTop: 8 }}>
                  <span className="tag">{r.category}</span>
                  <div style={{ marginTop: 4, fontSize: 12, color: "var(--text)" }}>{r.text}</div>
                </div>
              ))}

              <div className="panel-footer" style={{ marginTop: 12 }}>
                <button className="btn btn-primary" onClick={handleFinalize}>
                  <Check size={12} /> Finalize & Merge
                </button>
              </div>
            </>
          )}

          {status === "finalized" && (
            <div className="panel-center">
              <Check size={24} style={{ color: "var(--green)" }} />
              <p>Today's learnings have been merged into the global knowledge pool.</p>
            </div>
          )}
        </div>
      </div>

      {/* History */}
      <div className="gathering-history">
        <Clock size={14} /> Past Roundups
      </div>
    </div>
  );
}
