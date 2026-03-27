import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  BookOpen, Brain, Clock, Layers, Check, X, Edit3,
  ChevronDown, ChevronRight, Plus, Shield,
} from "lucide-react";
import "./Knowledge.css";

const CATEGORY_ICONS = {
  null_safety: Shield, error_handling: Shield, performance: Clock,
  testing: Check, api_design: Layers, architecture: Layers,
  security: Shield, style: Edit3, naming: Edit3, docs: BookOpen,
  domain_knowledge: Brain, pattern: Brain, gotcha: Shield, team: Layers,
};

export default function Knowledge() {
  const [learnings, setLearnings] = useState([]);
  const [tab, setTab] = useState("pool");
  const [loading, setLoading] = useState(true);
  const [collapsed, setCollapsed] = useState({});
  const [addText, setAddText] = useState("");
  const [addCategory, setAddCategory] = useState("domain_knowledge");

  const fetchData = async () => {
    setLoading(true);
    try { setLearnings(await api.getLearnings()); } catch (err) { console.error(err); }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  const handleApprove = async (id) => { await api.approveLearning(id); fetchData(); };
  const handleDismiss = async (id) => { await api.dismissLearning(id); fetchData(); };

  const handleAdd = async () => {
    if (!addText.trim()) return;
    await fetch("http://localhost:8420/api/learnings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rule: addText, category: addCategory }),
    });
    setAddText("");
    fetchData();
  };

  const active = learnings.filter((l) => l.status === "active");
  const pending = learnings.filter((l) => l.status === "pending");

  // Group by category
  const byCategory = {};
  const items = tab === "pending" ? pending : learnings.filter((l) => l.status !== "dismissed");
  for (const l of items) {
    (byCategory[l.category] = byCategory[l.category] || []).push(l);
  }

  const toggleCategory = (cat) => setCollapsed((c) => ({ ...c, [cat]: !c[cat] }));

  if (loading) return <p className="page-empty">Loading...</p>;

  return (
    <div className="knowledge-page">
      {/* Tabs */}
      <div className="knowledge-tabs">
        <button className={`inbox-tab ${tab === "pool" ? "active" : ""}`} onClick={() => setTab("pool")}>
          Knowledge Pool
        </button>
        <button className={`inbox-tab ${tab === "pending" ? "active" : ""}`} onClick={() => setTab("pending")}>
          Needs Review {pending.length > 0 && <span className="tab-badge">{pending.length}</span>}
        </button>
      </div>

      {/* Stats row */}
      {tab === "pool" && (
        <div className="knowledge-stats">
          <span className="kstat"><Brain size={12} /> {active.length} active</span>
          <span className="kstat"><Clock size={12} /> {pending.length} pending</span>
          <span className="kstat"><Layers size={12} /> {Object.keys(byCategory).length} categories</span>
        </div>
      )}

      {Object.keys(byCategory).length === 0 ? (
        <div className="empty-state">
          <Brain size={36} className="empty-icon" />
          <div className="empty-title">{tab === "pending" ? "All caught up" : "No learnings yet"}</div>
          <div className="empty-sub">
            {tab === "pending"
              ? "No learnings waiting for review"
              : "Learnings are discovered from PR comments, agent feedback, and manual input"}
          </div>
        </div>
      ) : (
        <div className="category-sections">
          {Object.entries(byCategory).sort().map(([category, items]) => {
            const Icon = CATEGORY_ICONS[category] || Brain;
            const isCollapsed = collapsed[category];
            const pendingCount = items.filter((l) => l.status === "pending").length;

            return (
              <div key={category} className="category-section">
                <div className="category-header" onClick={() => toggleCategory(category)}>
                  {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  <Icon size={14} />
                  <span className="category-name">{category.replace(/_/g, " ")}</span>
                  {pendingCount > 0 && <span className="tab-badge">{pendingCount} needs review</span>}
                  <span className="category-count">{items.length}</span>
                </div>

                {!isCollapsed && (
                  <div className="category-items">
                    {items.map((l) => (
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
                        <span className="learning-rule">{l.rule}</span>
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

      {/* Add manual learning */}
      <div className="add-learning-section">
        <div className="add-learning-header"><Plus size={12} /> Add a learning</div>
        <div className="add-learning-form">
          <input
            value={addText}
            onChange={(e) => setAddText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            placeholder="e.g. Always use connection pooling for batch operations"
          />
          <select value={addCategory} onChange={(e) => setAddCategory(e.target.value)}>
            {Object.keys(CATEGORY_ICONS).map((c) => (
              <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
            ))}
          </select>
          <button className="btn" onClick={handleAdd}><Check size={12} /></button>
        </div>
      </div>
    </div>
  );
}
