import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  BookOpen, Brain, Clock, Layers, Check, X, Edit3,
  ChevronDown, ChevronRight, Plus, Shield, Download, Loader, Sparkles,
  GraduationCap,
} from "lucide-react";
import { relativeTime } from "../utils/dates";
import InfoButton from "../components/InfoButton";
import ConfirmModal from "../components/ConfirmModal";
import BackfillProgress from "../components/BackfillProgress";
import Training from "./Training";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import "./Knowledge.css";
import LearningProvenance from "./brain/LearningProvenance";

const CATEGORY_ICONS = {
  null_safety: Shield, error_handling: Shield, performance: Clock,
  testing: Check, api_design: Layers, architecture: Layers,
  security: Shield, style: Edit3, naming: Edit3, docs: BookOpen,
  domain_knowledge: Brain, pattern: Brain, gotcha: Shield, team: Layers,
};

export default function BrainView() {
  const defaultOrg = useDefaultOrg();
  const [learnings, setLearnings] = useState([]);
  const [rawSignals, setRawSignals] = useState([]);
  const [rawSignalsTotal, setRawSignalsTotal] = useState(0);
  const [kLoading, setKLoading] = useState(true);
  const [expandedCats, setExpandedCats] = useState({});
  const [addText, setAddText] = useState("");
  const [addCategory, setAddCategory] = useState("domain_knowledge");
  const [backfilling, setBackfilling] = useState(false);
  const [backfillProgress, setBackfillProgress] = useState(null);
  const [expandedLearning, setExpandedLearning] = useState(null);
  const [provenanceCache, setProvenanceCache] = useState({});
  const [provenanceLoading, setProvenanceLoading] = useState({});
  const [showBackfillModal, setShowBackfillModal] = useState(false);
  const [backfillLimit, setBackfillLimit] = useState("20");
  const [backfillRepo, setBackfillRepo] = useState("");
  const [confirmingBackfill, setConfirmingBackfill] = useState(false);
  const [startingBackfill, setStartingBackfill] = useState(false);
  const [configuredRepos, setConfiguredRepos] = useState([]);
  // Tab state persists through the URL. Valid tabs: pool, pending,
  // unsynthesized, training. (The old "queue" tab was retired — the
  // unprocessed-pupdate queue now opens as a modal from the Brain
  // widget on Home.)
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTabState] = useState(() => {
    const urlTab = searchParams.get("tab");
    return ["pool", "pending", "unsynthesized", "training"].includes(urlTab) ? urlTab : "pool";
  });
  const setTab = (next) => {
    setTabState(next);
    const params = new URLSearchParams(searchParams);
    if (next === "pool") params.delete("tab");
    else params.set("tab", next);
    setSearchParams(params, { replace: true });
  };
  const [synthesizing, setSynthesizing] = useState(false);
  // Bulk-dismiss-thin flow: click Clean up → fetch dry_run preview →
  // ConfirmModal opens with count + sample → user confirms → real dismiss.
  // null = idle, object = preview loaded and modal open.
  const [cleanupPreview, setCleanupPreview] = useState(null);
  const [cleaningUp, setCleaningUp] = useState(false);
  // Scope filter: "all" / "global" / "scoped" / "unscoped". Purely a
  // view-layer filter — doesn't touch the learning rows themselves.
  const [scopeFilter, setScopeFilter] = useState("all");
  // RAG retrieval health. {backend, model, rules_indexed,
  // rules_total_active, ready_pct} or null while loading. backend
  // is null when no embedding library is installed / configured —
  // retrieval silently returns [] in that state, so we surface it
  // in the stats row so the user knows the layer's offline.
  const [ragStatus, setRagStatus] = useState(null);

  const fetchLearnings = async () => {
    setKLoading(true);
    try {
      // Fetch active + pending separately. The /learnings endpoint
      // sorts by confidence desc and caps at 500 — without splitting
      // by status, active rules (which start at higher confidence)
      // can crowd pending rules out of the page entirely. With ~600
      // pending in the DB, the unfiltered list returned 0 of them
      // and the Pending tab read as empty even though /brain/status
      // showed 590 waiting.
      const [active, pending, sigs, sigCount] = await Promise.all([
        api.getLearnings({ status: "active", limit: 500 }),
        api.getLearnings({ status: "pending", limit: 500 }),
        api.getSignals({ synthesized: false, limit: 500 }).catch(() => []),
        api.getSignalsCount({ synthesized: false }).catch(() => ({ count: 0 })),
      ]);
      setLearnings([...(active || []), ...(pending || [])]);
      setRawSignals(sigs);
      setRawSignalsTotal(sigCount?.count ?? 0);
    } catch (err) { console.error(err); }
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
    // RAG status. Quiet failure — the pill just hides if we couldn't
    // reach the endpoint. Re-fetched after a backfill kickoff so the
    // indexed-count reflects the new state.
    api.getRagStatus().then(setRagStatus).catch(() => setRagStatus(null));
  }, []);

  // Refetch the queue whenever the Queue tab is active. Drains fast
  // (brain cycle triages these every minute) so the list churns — a
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
                ? `${formatRepo(x.repo, defaultOrg)}: error (${x.error.slice(0, 40)})`
                : `${formatRepo(x.repo, defaultOrg)}: ${x.signals_created} signals from ${x.comments_scanned} comments`)
              .join("\n");
            if (r.signals_created === 0 && errored.length === 0) {
              showToast("No new PR comments found.\n" + summary, "normal");
            } else if (r.signals_created === 0) {
              showToast("Backfill errors:\n" + summary, "high");
            } else {
              const note = errored.length ? ` (${errored.length} repo errors)` : "";
              const mergedNote = r.learnings_merged ? `, merged ${r.learnings_merged} duplicates` : "";
              showToast(`Synthesized ${r.synthesized} into ${r.new_learnings} learnings${mergedNote}${note}\n${summary}`, errored.length ? "high" : "normal");
            }
          }
          fetchLearnings();
          // Let the final panel linger briefly so the user sees the
          // done/error state, then clear it.
          setTimeout(() => setBackfillProgress(null), 6000);
        }
      } catch {
        // transient network errors — keep polling
      }
    };
    tick();
    const interval = setInterval(tick, 1500);
    return () => { cancelled = true; clearInterval(interval); };
  }, [backfilling]);

  // Learnings with category="pattern" are now legitimate — the LLM
  // chose "pattern" as the real bucket. "Unsynthesized" is reserved
  // for raw signals that haven't been through LLM synthesis yet;
  // those live in a separate table and show up here as signal-shaped
  // rows so the user can see what's waiting for the queue.
  const active = learnings.filter((l) => l.status === "active");
  const pending = learnings.filter((l) => l.status === "pending");

  // Normalize signals to learning-shape so the existing category
  // rendering works without branching. Status "unsynthesized" is a
  // UI-only marker; these rows can't be approved, only deleted.
  const unsynthesized = rawSignals.map((s) => ({
    id: `signal-${s.id}`,
    _signal_id: s.id,
    rule: s.text,
    category: s.category || "pattern",
    scope_repo: s.repo,
    scope_language: s.language,
    is_global: false,
    confidence: 0,
    signal_count: 1,
    source: s.source_type,
    status: "unsynthesized",
  }));

  const baseVisible = tab === "unsynthesized"
    ? unsynthesized
    : tab === "pending"
      ? pending
      : learnings.filter((l) => l.status !== "dismissed");

  // Scope filter pills: "all" (default) / "global" (is_global=true) /
  // "scoped" (has a scope_repo) / "unscoped" (neither). The filter is
  // most useful on the Pool tab where a mixed bag of rules arrives —
  // but it also works on pending/unsynthesized without getting in the way.
  const matchesScopeFilter = (l) => {
    if (scopeFilter === "all") return true;
    if (scopeFilter === "global") return !!l.is_global;
    if (scopeFilter === "scoped") return !l.is_global && !!l.scope_repo;
    if (scopeFilter === "unscoped") return !l.is_global && !l.scope_repo;
    return true;
  };
  const visible = baseVisible.filter(matchesScopeFilter);
  const scopeCounts = {
    all: baseVisible.length,
    global: baseVisible.filter((l) => l.is_global).length,
    scoped: baseVisible.filter((l) => !l.is_global && l.scope_repo).length,
    unscoped: baseVisible.filter((l) => !l.is_global && !l.scope_repo).length,
  };

  const byCategory = {};
  for (const l of visible) {
    (byCategory[l.category] = byCategory[l.category] || []).push(l);
  }

  const handleApprove = async (id) => { await api.approveLearning(id); fetchLearnings(); };
  const handleDismiss = async (id) => { await api.dismissLearning(id); fetchLearnings(); };
  const handleToggleGlobal = async (l) => {
    // Flip the global flag. Flipping to global clears scope_repo
    // server-side; flipping back leaves scope_repo null (the user
    // re-scopes manually if they need a specific repo).
    try {
      await api.updateLearning(l.id, { is_global: !l.is_global });
      fetchLearnings();
    } catch (err) {
      showToast("Couldn't update: " + (err.message || "unknown"), "high");
    }
  };

  const toggleProvenance = async (id) => {
    // Collapse if already expanded.
    if (expandedLearning === id) {
      setExpandedLearning(null);
      return;
    }
    setExpandedLearning(id);
    // Lazy-fetch signals the first time a learning is expanded.
    if (provenanceCache[id] === undefined) {
      setProvenanceLoading((s) => ({ ...s, [id]: true }));
      try {
        const data = await api.getLearning(id);
        setProvenanceCache((s) => ({ ...s, [id]: data?.signals || [] }));
      } catch {
        setProvenanceCache((s) => ({ ...s, [id]: [] }));
      } finally {
        setProvenanceLoading((s) => ({ ...s, [id]: false }));
      }
    }
  };
  const handleApproveAll = async () => {
    const count = pending.length;
    // Parallel — sequential await on 50+ pending was one HTTP per
    // round-trip and made the UI hang for ~30s. Server endpoints are
    // independent per-id so order doesn't matter.
    await Promise.all(pending.map((l) => api.approveLearning(l.id)));
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
    showToast(`Synthesizing up to 50 signals...`, "normal");
    try {
      const result = await api.classifyLearnings(50);
      const parts = [];
      if (result.synthesized) parts.push(`${result.synthesized} synthesized`);
      if (result.new_learnings) parts.push(`${result.new_learnings} new learnings`);
      if (result.dropped_junk) parts.push(`${result.dropped_junk} dropped as junk`);
      if (result.remaining) parts.push(`${result.remaining} still queued`);
      showToast(parts.length ? parts.join(", ") : "Nothing to synthesize", "normal");
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
        {/* Live progress while backfill is running */}
        {(backfilling || backfillProgress?.phase === "done" || backfillProgress?.phase === "error") && (
          <BackfillProgress progress={backfillProgress} />
        )}
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
            Unsynthesized {rawSignalsTotal > 0 && <span className="tab-badge">{rawSignalsTotal}</span>}
          </button>
          <button
            className={`inbox-tab ${tab === "training" ? "active" : ""}`}
            onClick={() => setTab("training")}
          >
            <GraduationCap size={11} /> Training
          </button>
        </div>

        {tab === "training" && <Training />}

        {/* Everything below is the learnings/signals surface — hide it on
            the Training tab which has its own content above. */}
        {tab !== "training" && <>
        {tab === "unsynthesized" && unsynthesized.length > 0 && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 0", marginBottom: 8 }}>
            <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0, flex: 1 }}>
              Raw PR-comment signals waiting for LLM synthesis ({rawSignalsTotal} total{rawSignalsTotal > unsynthesized.length ? `, showing ${unsynthesized.length}` : ""}). The brain cycle drains the queue automatically, one batch per tick. Click "Synthesize Now" to run a batch immediately.
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
          {ragStatus !== null && (() => {
            // Three states: offline (no backend installed), partial
            // (backfill in flight), ready (every active rule has an
            // embedding). Click on offline to surface install steps
            // — the hover tooltip is too easy to miss.
            const offline = !ragStatus.backend;
            const indexed = ragStatus.rules_indexed || 0;
            const total = ragStatus.rules_total_active || 0;
            const partial = !offline && total > 0 && indexed < total;
            let label, title;
            if (offline) {
              label = "RAG offline — click for setup";
              title = "Click for install instructions";
            } else if (total === 0) {
              label = "RAG: no rules";
              title = `Backend: ${ragStatus.model || "(unknown)"}. No active rules yet — retrieval will be empty until rules graduate.`;
            } else if (partial) {
              label = `RAG: ${indexed}/${total} indexed`;
              title = `Backend: ${ragStatus.model}. Backfill in progress — ${total - indexed} rules still need scenario descriptions before retrieval picks them up.`;
            } else {
              label = `RAG ready: ${indexed} indexed`;
              title = `Backend: ${ragStatus.model}. All ${total} active rules indexed for retrieval.`;
            }
            const ragSetupMessage = (
              "RAG (rule retrieval) needs an embedding backend. Three options:\n\n" +
              "1. Local model (free, ~2GB download):\n" +
              "   pip install -e \".[rag]\"\n" +
              "   Restart `maiko serve`.\n\n" +
              "2. Voyage (Anthropic-aligned):\n" +
              "   pip install voyageai\n" +
              "   export VOYAGE_API_KEY=your-key\n" +
              "   Restart `maiko serve`.\n\n" +
              "3. OpenAI:\n" +
              "   pip install openai\n" +
              "   export OPENAI_API_KEY=your-key\n" +
              "   Restart `maiko serve`."
            );
            return (
              <span
                className={`kstat${offline ? " kstat-warning kstat-clickable" : ""}`}
                title={title}
                onClick={offline ? () => alert(ragSetupMessage) : undefined}
                style={offline ? { cursor: "pointer" } : undefined}
              >
                <Sparkles size={12} /> {label}
              </span>
            );
          })()}
          {pending.length > 0 && (
            <>
              <button className="btn btn-sm" onClick={handleApproveAll} style={{ marginLeft: "auto" }}>
                <Check size={10} /> Approve All ({pending.length})
              </button>
              <button
                className="btn btn-sm"
                onClick={async () => {
                  // Fetch the preview (dry_run) so the user sees how
                  // many would be affected before confirming. Common
                  // case: a clustering pass produced lots of one-
                  // signal singletons that aren't real rules; this
                  // drains them without touching anything substantive.
                  try {
                    const preview = await api.bulkDismissPendingLearnings({
                      max_signal_count: 1,
                      older_than_days: 14,
                      dry_run: true,
                    });
                    if ((preview?.count || 0) === 0) {
                      showToast("No thin pending learnings to clean up.", "normal");
                      return;
                    }
                    setCleanupPreview(preview);
                  } catch (err) {
                    showToast("Couldn't load preview: " + (err.message || "unknown"), "high");
                  }
                }}
                title="Dismiss pending learnings with one or zero signals that are older than 14 days. Usually noise from clustering."
              >
                <Sparkles size={10} /> Clean up thin
              </button>
              <button className="btn btn-sm btn-danger" onClick={async () => {
                const count = pending.length;
                // Parallel — sequential await on 50+ pending hung the UI.
                await Promise.all(pending.map((l) => api.dismissLearning(l.id)));
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
                    ? `Scanning ${formatRepo(backfillProgress.current_repo || "", defaultOrg)} (${backfillProgress.repos_done}/${backfillProgress.repos_total})`
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

        {baseVisible.length > 0 && (
          <div className="scope-filter-row">
            {[
              { key: "all", label: "All" },
              { key: "global", label: "Global" },
              { key: "scoped", label: "Scoped" },
              { key: "unscoped", label: "Unscoped" },
            ].map((o) => (
              <button
                key={o.key}
                type="button"
                className={`scope-filter-pill ${scopeFilter === o.key ? "active" : ""}`}
                onClick={() => setScopeFilter(o.key)}
                disabled={scopeCounts[o.key] === 0 && o.key !== "all"}
                title={
                  o.key === "global" ? "Applies to every repo (seen in 3+ repos or flipped by hand)"
                  : o.key === "scoped" ? "Pinned to a single repo"
                  : o.key === "unscoped" ? "No scope set — matches every lookup"
                  : "Everything"
                }
              >
                {o.label} <span className="scope-filter-count">{scopeCounts[o.key]}</span>
              </button>
            ))}
          </div>
        )}

        {kLoading ? (
          <p className="page-empty">Loading...</p>
        ) : Object.keys(byCategory).length === 0 ? (
          <div className="empty-state">
            <Brain size={36} className="empty-icon" />
            <div className="empty-title">
              {baseVisible.length === 0
                ? "The brain's still fresh"
                : `Nothing here with scope: ${scopeFilter}`}
            </div>
            <div className="empty-sub">
              {baseVisible.length === 0
                ? "Learnings show up here as your agents review PRs, notice patterns, and get feedback. Give it a few cycles, or add one by hand."
                : "Try a different scope filter."}
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
                        <div key={l.id}>
                        <div
                          className={`learning-row status-${l.status} ${expandedLearning === l.id ? "expanded" : ""}`}
                          onClick={() => l.status !== "unsynthesized" && toggleProvenance(l.id)}
                          style={{ cursor: l.status === "unsynthesized" ? "default" : "pointer" }}
                        >
                          <ChevronRight
                            size={12}
                            className={`learning-chevron ${expandedLearning === l.id ? "open" : ""}`}
                          />
                          <div className="learning-left">
                            {l.status === "pending" && <span className="badge paused">pending</span>}
                            {l.status === "unsynthesized" && <span className="badge" title="Raw PR-comment signal waiting for LLM synthesis">raw</span>}
                            {l.source && <span className="tag">{l.source}</span>}
                            {l.is_global ? (
                              <span className="tag tag-global" title="Seen in 3+ repos — feeds every LoRA">🌐 global</span>
                            ) : l.scope_repo ? (
                              <span className="tag" title={l.scope_repo}>{formatRepo(l.scope_repo, defaultOrg)}</span>
                            ) : (
                              <span className="tag" title="No scope set — matches every lookup">unscoped</span>
                            )}
                            {l.scope_language && <span className="tag">{l.scope_language}</span>}
                          </div>
                          <div className="confidence-bar-wrapper">
                            <div className="confidence-bar" style={{ width: `${l.confidence * 100}%` }} />
                          </div>
                          <span className="signal-count">{l.signal_count} signal{l.signal_count === 1 ? "" : "s"}</span>
                          <span className={`learning-rule ${expandedLearning === l.id ? "expanded" : ""}`}>
                            {l.rule}
                          </span>
                          <div className="learning-btns" onClick={(e) => e.stopPropagation()}>
                            {l.status === "pending" && (
                              <button className="btn btn-sm" onClick={() => handleApprove(l.id)}>
                                <Check size={10} /> Approve
                              </button>
                            )}
                            {(l.status === "active" || l.status === "pending") && (
                              <button
                                className="btn btn-sm"
                                onClick={() => handleToggleGlobal(l)}
                                title={l.is_global
                                  ? "This rule applies to every repo. Click to un-global it (leaves scope blank)."
                                  : "Make this rule apply to every repo."}
                              >
                                🌐 {l.is_global ? "Un-global" : "Make global"}
                              </button>
                            )}
                            {l.status === "active" || l.status === "pending" ? (
                              <button className="btn btn-sm btn-danger" onClick={() => handleDismiss(l.id)}>
                                <X size={10} />
                              </button>
                            ) : null}
                          </div>
                        </div>
                        {expandedLearning === l.id && (
                          <>
                            {l.violation_description && (
                              <div className="learning-scenario">
                                <div className="learning-scenario-label">
                                  <Sparkles size={10} /> Scenario this rule applies to
                                  <span
                                    className="learning-scenario-hint"
                                    title="This is the natural-language description retrieval matches against. Edit the rule text and the description regenerates from the signals."
                                  >
                                    matched against new diffs at review time
                                  </span>
                                </div>
                                <p className="learning-scenario-body">
                                  {l.violation_description}
                                </p>
                              </div>
                            )}
                            <LearningProvenance
                              loading={provenanceLoading[l.id]}
                              signals={provenanceCache[l.id]}
                              defaultOrg={defaultOrg}
                            />
                          </>
                        )}
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
        </>}
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
                Scans every inline (per-file) PR review comment in the selected repo(s). Each becomes a raw signal — paired with the diff hunk around the comment — that feeds classification, aggregation, and LoRA training. Summary "LGTM" bodies and conversation comments are skipped on purpose.
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
                  Max comments {backfillRepo ? "" : "per repo"} (optional cap)
                  <input
                    type="text"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    value={backfillLimit}
                    onChange={(e) => setBackfillLimit(e.target.value.replace(/[^0-9]/g, ""))}
                    placeholder="leave blank for all"
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
                  {backfilling ? <><Loader size={12} className="spin" /> Scanning...</> : <><Download size={12} /> Scan{backfillLimit ? ` up to ${backfillLimit} comments` : " all comments"}{backfillRepo ? ` in ${backfillRepo.split("/").pop()}` : ""}</>}
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
            This scrapes every inline PR review comment in {backfillRepo ? <code>{backfillRepo}</code> : "every configured repo"} via the <code>gh</code> CLI,
            then runs an LLM synthesis pass to turn the comments into clean rules.
          </p>
          <p>Typically a few minutes of wall time and a handful of LLM calls. You can keep working — it runs in the background.</p>
          <span className="confirm-estimate">
            {backfillLimit ? `≤ ${backfillLimit} comments` : "all comments"}{backfillRepo ? "" : " × each configured repo"} · ≤20 LLM calls for synthesis
          </span>
        </>}
        confirmText="Start backfill"
        busy={startingBackfill}
        onCancel={() => setConfirmingBackfill(false)}
        onConfirm={async () => {
          setStartingBackfill(true);
          // blank = no cap; any other input gets clamped to a sane range.
          const trimmed = backfillLimit.trim();
          const limit = trimmed ? Math.max(1, parseInt(trimmed, 10)) : null;
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

      <ConfirmModal
        open={cleanupPreview != null}
        title={`Dismiss ${cleanupPreview?.count || 0} thin pending learning${cleanupPreview?.count === 1 ? "" : "s"}?`}
        severity="danger"
        body={<>
          <p>
            These have <code>signal_count ≤ 1</code> and were created
            more than 14 days ago — usually one-off PR comments that
            auto-clustered into "rules" and never accumulated more
            evidence. Active rules + recent / well-supported pending
            ones are NOT touched.
          </p>
          {cleanupPreview?.sample?.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", fontSize: 11, color: "var(--text-dim)" }}>
                Sample ({Math.min(10, cleanupPreview.sample.length)} of {cleanupPreview.count})
              </summary>
              <ul style={{ marginTop: 6, paddingLeft: 16, fontSize: 11, color: "var(--text)" }}>
                {cleanupPreview.sample.map((l) => (
                  <li key={l.id}>{l.rule}</li>
                ))}
              </ul>
            </details>
          )}
        </>}
        confirmText={`Dismiss ${cleanupPreview?.count || 0}`}
        busy={cleaningUp}
        onCancel={() => setCleanupPreview(null)}
        onConfirm={async () => {
          setCleaningUp(true);
          try {
            const result = await api.bulkDismissPendingLearnings({
              max_signal_count: 1,
              older_than_days: 14,
              dry_run: false,
            });
            showToast(`Dismissed ${result.dismissed} thin learnings.`, "normal");
            setCleanupPreview(null);
            fetchLearnings();
          } catch (err) {
            showToast("Cleanup failed: " + (err.message || "unknown"), "high");
          } finally {
            setCleaningUp(false);
          }
        }}
      />
    </div>
  );
}
