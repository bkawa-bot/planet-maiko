import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  BookOpen, Brain, Clock, Layers, Check, X, Edit3,
  ChevronDown, ChevronRight, Plus, Shield, Download, Loader, Sparkles,
} from "lucide-react";
import InfoButton from "../components/InfoButton";
import ConfirmModal from "../components/ConfirmModal";
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
  const [expandedCats, setExpandedCats] = useState({});
  const [addText, setAddText] = useState("");
  const [addCategory, setAddCategory] = useState("domain_knowledge");
  const [backfilling, setBackfilling] = useState(false);
  const [backfillProgress, setBackfillProgress] = useState(null);
  const [expandedLearning, setExpandedLearning] = useState(null);
  const [showBackfillModal, setShowBackfillModal] = useState(false);
  const [backfillLimit, setBackfillLimit] = useState("20");
  const [backfillRepo, setBackfillRepo] = useState("");
  const [confirmingBackfill, setConfirmingBackfill] = useState(false);
  const [startingBackfill, setStartingBackfill] = useState(false);
  const [configuredRepos, setConfiguredRepos] = useState([]);
  const [tab, setTab] = useState("pool");
  const [synthesizing, setSynthesizing] = useState(false);

  const fetchLearnings = async () => {
    setKLoading(true);
    try { setLearnings(await api.getLearnings()); } catch (err) { console.error(err); }
    setKLoading(false);
  };

  useEffect(() => {
    fetchLearnings();
    api.getConfig().then((c) => {
      setConfiguredRepos(c?.github?.repos || []);
    }).catch(() => {});
    // Resume showing progress if a backfill is already running (page reload)
    api.getBackfillStatus().then((s) => {
      if (s?.running) {
        setBackfilling(true);
        setBackfillProgress(s);
      }
    }).catch(() => {});
  }, []);

  // Poll backfill status while a job is running; surface final summary toast.
  useEffect(() => {
    if (!backfilling) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await api.getBackfillStatus();
        if (cancelled) return;
        setBackfillProgress(s);
        if (s?.phase === "done" || s?.phase === "error") {
          setBackfilling(false);
          if (s.phase === "error") {
            showToast(`Backfill failed: ${s.error || "unknown"}`, "high");
          } else {
            const r = s.result || {};
            const perRepo = r.per_repo || [];
            const errored = perRepo.filter((x) => x.error);
            const summary = perRepo
              .map((x) => x.error
                ? `${x.repo}: error (${x.error.slice(0, 40)})`
                : `${x.repo}: ${x.signals_created}/${x.prs_scanned} PRs`)
              .join("\n");
            if (r.signals_created === 0 && errored.length === 0) {
              showToast("No new PR comments found.\n" + summary, "normal");
            } else if (r.signals_created === 0) {
              showToast("Backfill errors:\n" + summary, "high");
            } else {
              const note = errored.length ? ` (${errored.length} repo errors)` : "";
              showToast(`Synthesized ${r.synthesized} into ${r.new_learnings} learnings${note}\n${summary}`, errored.length ? "high" : "normal");
            }
          }
          fetchLearnings();
        }
      } catch {
        // transient network errors — keep polling
      }
    };
    tick();
    const interval = setInterval(tick, 1500);
    return () => { cancelled = true; clearInterval(interval); };
  }, [backfilling]);

  const active = learnings.filter((l) => l.status === "active" && l.category !== "pattern");
  const pending = learnings.filter((l) => l.status === "pending" && l.category !== "pattern");
  const unsynthesized = learnings.filter((l) => l.category === "pattern" && l.status !== "dismissed");

  const visible = tab === "unsynthesized"
    ? unsynthesized
    : tab === "pending"
      ? pending
      : learnings.filter((l) => l.status !== "dismissed" && l.category !== "pattern");

  const byCategory = {};
  for (const l of visible) {
    (byCategory[l.category] = byCategory[l.category] || []).push(l);
  }

  const handleApprove = async (id) => { await api.approveLearning(id); fetchLearnings(); };
  const handleDismiss = async (id) => { await api.dismissLearning(id); fetchLearnings(); };
  const handleApproveAll = async () => {
    const count = pending.length;
    for (const l of pending) {
      await api.approveLearning(l.id);
    }
    showToast(`Approved ${count} learnings`, "normal");
    fetchLearnings();
  };

  const handleAdd = async () => {
    if (!addText.trim()) return;
    await api.createLearning({ rule: addText, category: addCategory });
    setAddText("");
    fetchLearnings();
  };

  const handleSynthesize = async () => {
    setSynthesizing(true);
    showToast(`Synthesizing ${unsynthesized.length} learnings...`, "normal");
    try {
      const result = await api.classifyLearnings(50);
      showToast(`Reclassified ${result.classified_learnings || 0} learnings, ${result.classified_signals || 0} signals`, "normal");
      fetchLearnings();
    } catch (err) {
      showToast("Synthesis failed: " + err.message, "high");
    }
    setSynthesizing(false);
  };

  const toggleCategory = (cat) => setExpandedCats((e) => ({ ...e, [cat]: !e[cat] }));

  return (
    <div className="brain-view-page">
      <div className="knowledge-page">
        {/* Tabs */}
        <div className="knowledge-tabs">
          <button
            className={`inbox-tab ${tab === "pool" ? "active" : ""}`}
            onClick={() => setTab("pool")}
          >
            Knowledge Pool
          </button>
          <button
            className={`inbox-tab ${tab === "pending" ? "active" : ""}`}
            onClick={() => setTab("pending")}
          >
            Pending {pending.length > 0 && <span className="tab-badge">{pending.length}</span>}
          </button>
          <button
            className={`inbox-tab ${tab === "unsynthesized" ? "active" : ""}`}
            onClick={() => setTab("unsynthesized")}
          >
            Unsynthesized {unsynthesized.length > 0 && <span className="tab-badge">{unsynthesized.length}</span>}
          </button>
        </div>

        {tab === "unsynthesized" && unsynthesized.length > 0 && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 0", marginBottom: 8 }}>
            <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0, flex: 1 }}>
              These are raw signals from PR comments waiting to be classified into proper categories. They run through the brain cycle automatically, or you can synthesize them now.
            </p>
            <button
              className="btn btn-primary btn-sm"
              disabled={synthesizing}
              onClick={handleSynthesize}
            >
              {synthesizing ? <><Loader size={10} className="spin" /> Synthesizing...</> : <><Sparkles size={10} /> Synthesize Now</>}
            </button>
          </div>
        )}

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
                const count = pending.length;
                for (const l of pending) {
                  await api.dismissLearning(l.id);
                }
                showToast(`Dismissed ${count} learnings`, "normal");
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
            onClick={() => setShowBackfillModal(true)}
            title={backfilling && backfillProgress ? `Phase: ${backfillProgress.phase}` : ""}
          >
            {backfilling ? (
              <>
                <Loader size={10} className="spin" />
                {" "}
                {backfillProgress?.phase === "synthesizing" ? "Synthesizing..."
                  : backfillProgress?.phase === "aggregating" ? "Aggregating..."
                  : backfillProgress?.repos_total
                    ? `Scanning ${backfillProgress.current_repo || ""} (${backfillProgress.repos_done}/${backfillProgress.repos_total})`
                    : "Starting..."}
              </>
            ) : (
              <><Download size={10} /> Backfill from PRs</>
            )}
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
            <p>Each learning has a confidence score (the colored bar). It starts low and increases with each confirming signal. If the pre-commit hook flags code and the developer bypasses it, confidence decreases.</p>
            <h4>How learnings are used</h4>
            <p>Active learnings become training data for the LoRA compliance model. Run <code>maiko retrain</code> to generate synthetic examples and fine-tune the model on your team's rules.</p>
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
              const isExpanded = !!expandedCats[category];
              const pendingCount = catItems.filter((l) => l.status === "pending").length;

              return (
                <div key={category} className="category-section">
                  <div className="category-header" onClick={() => toggleCategory(category)}>
                    {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <Icon size={14} />
                    <span className="category-name">{category.replace(/_/g, " ")}</span>
                    {pendingCount > 0 && <span className="tab-badge">{pendingCount} needs review</span>}
                    <span className="category-count">{catItems.length}</span>
                  </div>

                  {isExpanded && (
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

      {/* Backfill modal */}
      {showBackfillModal && (
        <div className="modal-overlay" onClick={() => setShowBackfillModal(false)}>
          <div className="generated-tasks-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Download size={14} />
              <span>Backfill from PRs</span>
              <button className="btn btn-sm" onClick={() => setShowBackfillModal(false)} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <div className="modal-body">
              <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12 }}>
                Scans recent merged PRs for review comments. Each comment becomes a raw signal that gets classified and aggregated into learnings.
              </p>
              <div className="form-row">
                <label>
                  Repo
                  <select
                    value={backfillRepo}
                    onChange={(e) => setBackfillRepo(e.target.value)}
                  >
                    <option value="">All configured repos</option>
                    {configuredRepos.map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="form-row">
                <label>
                  PRs to scan {backfillRepo ? "" : "per repo"}
                  <input
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={backfillLimit}
                    onChange={(e) => setBackfillLimit(e.target.value.replace(/[^0-9]/g, ""))}
                    placeholder="20"
                    autoFocus
                  />
                </label>
              </div>
              <div className="form-actions">
                <button type="button" className="btn" onClick={() => setShowBackfillModal(false)}>Cancel</button>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={backfilling}
                  onClick={() => setConfirmingBackfill(true)}
                >
                  {backfilling ? <><Loader size={12} className="spin" /> Scanning...</> : <><Download size={12} /> Scan {backfillLimit || 20} PRs{backfillRepo ? ` in ${backfillRepo.split("/").pop()}` : ""}</>}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <ConfirmModal
        open={confirmingBackfill}
        title="Backfill is resource-intensive"
        body={<>
          <p>
            This scans merged PRs in {backfillRepo ? <code>{backfillRepo}</code> : "every configured repo"} via the <code>gh</code> CLI,
            then runs an LLM synthesis pass to turn review comments into clean rules.
          </p>
          <p>Typically a few minutes of wall time and a handful of LLM calls. You can keep working — it runs in the background.</p>
          <span className="confirm-estimate">
            ~{backfillLimit || 20} PRs{backfillRepo ? "" : " × each configured repo"} · ≤20 LLM calls for synthesis
          </span>
        </>}
        confirmText="Start backfill"
        busy={startingBackfill}
        onCancel={() => setConfirmingBackfill(false)}
        onConfirm={async () => {
          setStartingBackfill(true);
          const limit = Math.min(500, Math.max(1, parseInt(backfillLimit, 10) || 20));
          try {
            await api.backfillKnowledge(limit, backfillRepo || null);
            setConfirmingBackfill(false);
            setShowBackfillModal(false);
            setBackfilling(true);
            showToast("Backfill started 🐾", "normal");
          } catch (err) {
            showToast("Backfill failed to start: " + err.message, "high");
          }
          setStartingBackfill(false);
        }}
      />
    </div>
  );
}
