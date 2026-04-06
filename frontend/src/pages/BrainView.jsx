import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  BookOpen, Brain, Clock, Layers, Check, X, Edit3,
  ChevronDown, ChevronRight, Plus, Shield, Download, Loader,
} from "lucide-react";
import InfoButton from "../components/InfoButton";
import "./Knowledge.css";

const CATEGORY_ICONS = {
  null_safety: Shield, error_handling: Shield, performance: Clock,
  testing: Check, api_design: Layers, architecture: Layers,
  security: Shield, style: Edit3, naming: Edit3, docs: BookOpen,
  domain_knowledge: Brain, pattern: Brain, gotcha: Shield, team: Layers,
};

export default function BrainView() {
  const [learnings, setLearnings] = useState([]);
  const [kLoading, setKLoading] = useState(true);
  const [collapsed, setCollapsed] = useState({});
  const [addText, setAddText] = useState("");
  const [addCategory, setAddCategory] = useState("domain_knowledge");
  const [backfilling, setBackfilling] = useState(false);
  const [expandedLearning, setExpandedLearning] = useState(null);

  const fetchLearnings = async () => {
    setKLoading(true);
    try { setLearnings(await api.getLearnings()); } catch (err) { console.error(err); }
    setKLoading(false);
  };

  useEffect(() => { fetchLearnings(); }, []);

  const handleApprove = async (id) => { await api.approveLearning(id); fetchLearnings(); };
  const handleDismiss = async (id) => { await api.dismissLearning(id); fetchLearnings(); };
  const handleApproveAll = async () => {
    for (const l of learnings.filter((l) => l.status === "pending")) {
      await api.approveLearning(l.id);
    }
    showToast(`Approved ${pending.length} learnings`, "normal");
    fetchLearnings();
  };

  const handleAdd = async () => {
    if (!addText.trim()) return;
    await api.createLearning({ rule: addText, category: addCategory });
    setAddText("");
    fetchLearnings();
  };

  const active = learnings.filter((l) => l.status === "active");
  const pending = learnings.filter((l) => l.status === "pending");
  const visible = learnings.filter((l) => l.status !== "dismissed");

  const byCategory = {};
  for (const l of visible) {
    (byCategory[l.category] = byCategory[l.category] || []).push(l);
  }

  const toggleCategory = (cat) => setCollapsed((c) => ({ ...c, [cat]: !c[cat] }));

  return (
    <div className="brain-view-page">
      <div className="knowledge-page">
        <div className="knowledge-stats">
          <span className="kstat"><Brain size={12} /> {active.length} active</span>
          <span className="kstat"><Clock size={12} /> {pending.length} pending</span>
          <span className="kstat"><Layers size={12} /> {Object.keys(byCategory).length} categories</span>
          {pending.length > 0 && (
            <>
              <button className="btn btn-sm" onClick={handleApproveAll} style={{ marginLeft: "auto" }}>
                <Check size={10} /> Approve All ({pending.length})
              </button>
              <button className="btn btn-sm btn-danger" onClick={async () => {
                for (const l of learnings.filter((l) => l.status === "pending")) {
                  await api.dismissLearning(l.id);
                }
                showToast(`Dismissed ${pending.length} learnings`, "normal");
                fetchLearnings();
              }}>
                <X size={10} /> Dismiss All
              </button>
            </>
          )}
          <button
            className="btn btn-sm"
            style={pending.length === 0 ? { marginLeft: "auto" } : {}}
            disabled={backfilling}
            onClick={async () => {
              setBackfilling(true);
              try {
                const result = await api.backfillKnowledge();
                if (result.signals_created === 0) {
                  showToast("No new PR comments found. Add review comments to your PRs first.", "normal");
                } else if (result.synth_note) {
                  showToast(`Found ${result.signals_created} comments. ${result.synth_note}`, "high");
                } else {
                  showToast(`Synthesized ${result.synthesized} comments into ${result.new_learnings} learnings`, "normal");
                }
                fetchLearnings();
              } catch (err) {
                showToast("Backfill failed: " + err.message, "high");
              }
              setBackfilling(false);
            }}
          >
            {backfilling ? <><Loader size={10} className="spin" /> Scanning...</> : <><Download size={10} /> Backfill from PRs</>}
          </button>
          <InfoButton title={<><Brain size={16} /> Knowledge Pool</>}>
            <p>The Knowledge Pool is Planet Maiko's collective memory — coding patterns and rules learned from your team's PR reviews, agent feedback, and manual input.</p>
            <h4>How learnings are created</h4>
            <ol>
              <li><strong>Signals</strong> — PR review comments, agent session feedback, and manual input create raw signals.</li>
              <li><strong>Aggregation</strong> — similar signals get grouped. Once enough accumulate (2-5 depending on category), they graduate into a learning.</li>
              <li><strong>Approval</strong> — high-stakes categories (security, API design, architecture) start as "pending" and need your approval before going active.</li>
            </ol>
            <h4>What confidence means</h4>
            <p>Each learning has a confidence score (the colored bar). It starts low and increases with each confirming signal. Tournament results also adjust confidence — learnings in winning agent sets get boosted.</p>
            <h4>How learnings are used</h4>
            <p>When an agent starts a task, <em>compile_brief()</em> selects the most relevant active learnings based on the repo, task type, and agent specialization. These become the agent's coding guidelines.</p>
          </InfoButton>
        </div>

        {kLoading ? (
          <p className="page-empty">Loading...</p>
        ) : Object.keys(byCategory).length === 0 ? (
          <div className="empty-state">
            <Brain size={36} className="empty-icon" />
            <div className="empty-title">No learnings yet</div>
            <div className="empty-sub">
              Learnings are discovered from PR comments, agent feedback, and manual input
            </div>
          </div>
        ) : (
          <div className="category-sections">
            {Object.entries(byCategory).sort().map(([category, catItems]) => {
              const Icon = CATEGORY_ICONS[category] || Brain;
              const isCollapsed = collapsed[category];
              const pendingCount = catItems.filter((l) => l.status === "pending").length;

              return (
                <div key={category} className="category-section">
                  <div className="category-header" onClick={() => toggleCategory(category)}>
                    {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                    <Icon size={14} />
                    <span className="category-name">{category.replace(/_/g, " ")}</span>
                    {pendingCount > 0 && <span className="tab-badge">{pendingCount} needs review</span>}
                    <span className="category-count">{catItems.length}</span>
                  </div>

                  {!isCollapsed && (
                    <div className="category-items">
                      {catItems.map((l) => (
                        <div key={l.id} className={`learning-row status-${l.status}`}>
                          <div className="learning-left">
                            {l.status === "pending" && <span className="badge paused">pending</span>}
                            {l.source && <span className="tag">{l.source}</span>}
                            {l.scope_repo && <span className="tag">{l.scope_repo}</span>}
                            {l.scope_language && <span className="tag">{l.scope_language}</span>}
                          </div>
                          <div className="confidence-bar-wrapper">
                            <div className="confidence-bar" style={{ width: `${l.confidence * 100}%` }} />
                          </div>
                          <span className="signal-count">{l.signal_count} signals</span>
                          <span className={`learning-rule ${expandedLearning === l.id ? "expanded" : ""}`} onClick={(e) => { e.stopPropagation(); setExpandedLearning(expandedLearning === l.id ? null : l.id); }}>
                            {l.rule}
                          </span>
                          <div className="learning-btns">
                            {l.status === "pending" && (
                              <button className="btn btn-sm" onClick={() => handleApprove(l.id)}>
                                <Check size={10} /> Approve
                              </button>
                            )}
                            {l.status !== "dismissed" && (
                              <button className="btn btn-sm btn-danger" onClick={() => handleDismiss(l.id)}>
                                <X size={10} />
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="add-learning-section">
          <div className="add-learning-header"><Plus size={12} /> Add a learning</div>
          <div className="add-learning-form">
            <input value={addText} onChange={(e) => setAddText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
              placeholder="e.g. Always use connection pooling for batch operations" />
            <select value={addCategory} onChange={(e) => setAddCategory(e.target.value)}>
              {Object.keys(CATEGORY_ICONS).map((c) => (
                <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
              ))}
            </select>
            <button className="btn" onClick={handleAdd}><Check size={12} /></button>
          </div>
        </div>
      </div>
    </div>
  );
}
