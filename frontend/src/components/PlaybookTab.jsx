import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { renderMarkdown } from "../utils/markdown";
import {
  Check, X, Edit3, Plus, RefreshCw, Loader, Map as MapIcon,
  ChevronDown, ChevronRight,
} from "@icons";
import "./PlaybookTab.css";

/**
 * Playbook tab: tribal / operational knowledge scoped per repo.
 *
 * Different intent from Learnings. Learnings are coding rules
 * retrieved by agents at task kickoff via `rules-relevant`. Insights
 * are workflow context injected verbatim into every new agent's
 * CLAUDE.md — tooling quirks, repo state, team conventions.
 *
 * Surfaces:
 *   - Pending queue (agent-authored, awaiting user approval)
 *   - Active playbook grouped by repo (confirm / edit / dismiss)
 *   - Manual "add a note" form (collapsed by default; clutters)
 */
export default function PlaybookTab({ onCountsChange }) {
  const [insights, setInsights] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("active"); // active | pending | all
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [addText, setAddText] = useState("");
  const [addRepo, setAddRepo] = useState("");
  const [addTags, setAddTags] = useState("");
  const [adding, setAdding] = useState(false);
  const [bulking, setBulking] = useState(false);
  const [cartographing, setCartographing] = useState(null);
  const [expandedOverviews, setExpandedOverviews] = useState({});

  const fetchAll = async () => {
    setLoading(true);
    try {
      const data = await api.getInsights({ status: "all" });
      setInsights(data);
      const pendingCount = data.filter((i) => i.status === "pending").length;
      onCountsChange?.(pendingCount);
    } catch (err) {
      console.error(err);
    }
    setLoading(false);
  };

  useEffect(() => { fetchAll(); }, []);
  useEffect(() => {
    // Profile lookup so "from agent-coding-planet-maiko-abc123" can
    // become "from Mochi". Best-effort; failures fall back to the raw id.
    api.getProfiles().then((ps) => setProfiles(Array.isArray(ps) ? ps : []))
      .catch(() => setProfiles([]));
  }, []);

  const profileById = useMemo(() => {
    const m = {};
    for (const p of profiles) m[p.id] = p;
    return m;
  }, [profiles]);

  const authorLabel = (agentId) => {
    if (!agentId) return null;
    const p = profileById[agentId];
    if (p?.display_name) return p.display_name;
    // Fallback: strip the "agent-<role>-" prefix and the trailing hex.
    return agentId.replace(/^agent-[^-]+-/, "").replace(/-[a-f0-9]{4,8}$/, "");
  };

  const approve = async (id) => {
    await api.approveInsight(id);
    showToast("Added to playbook", "normal");
    fetchAll();
  };

  const dismiss = async (id) => {
    await api.dismissInsight(id);
    fetchAll();
  };

  const confirm = async (id) => {
    await api.confirmInsight(id);
    showToast("Confirmed", "normal");
    fetchAll();
  };

  const startEdit = (insight) => {
    setEditingId(insight.id);
    setEditText(insight.text);
  };

  const saveEdit = async (id) => {
    if (!editText.trim()) return;
    await api.updateInsight(id, { text: editText.trim() });
    setEditingId(null);
    setEditText("");
    fetchAll();
  };

  const makeGlobal = async (insight) => {
    try {
      await api.updateInsight(insight.id, { repo_scope: null });
      showToast("Marked global", "normal");
      fetchAll();
    } catch (err) {
      showToast("Couldn't mark global: " + (err.message || "unknown"), "high");
    }
  };

  const handleCartograph = async (repo) => {
    if (!repo || repo === "(global)" || cartographing) return;
    setCartographing(repo);
    try {
      const result = await api.cartographRepo(repo);
      showToast(`${result.profile_name || "Atlas"} is walking ${repo} — check back in a few minutes`, "normal");
    } catch (err) {
      showToast(err.message || "Couldn't spawn cartographer", "high");
    }
    setCartographing(null);
  };

  const handleAdd = async () => {
    const text = addText.trim();
    if (!text) return;
    setAdding(true);
    try {
      await api.createInsight({
        text,
        repo_scope: addRepo.trim() || null,
        tags: addTags.split(",").map((t) => t.trim()).filter(Boolean),
        status: "active",
      });
      setAddText("");
      setAddTags("");
      showToast("Insight added", "normal");
      fetchAll();
    } catch (err) {
      showToast(err.message || "Couldn't add insight", "high");
    }
    setAdding(false);
  };

  // Partition by status + group by repo_scope.
  const pending = insights.filter((i) => i.status === "pending");
  const active = insights.filter((i) => i.status === "active");
  const visible = filter === "pending" ? pending
    : filter === "active" ? active
    : insights;

  const approveAllPending = async () => {
    if (bulking || pending.length === 0) return;
    if (!window.confirm(`Approve all ${pending.length} pending insights?`)) return;
    setBulking(true);
    try {
      // Sequential so the server isn't slammed; the list is bounded.
      for (const ins of pending) {
        await api.approveInsight(ins.id);
      }
      showToast(`Approved ${pending.length}`, "normal");
      fetchAll();
    } catch (err) {
      showToast(err.message || "Bulk approve failed", "high");
    }
    setBulking(false);
  };

  const byRepo = {};
  for (const i of visible) {
    const key = i.repo_scope || "(global)";
    (byRepo[key] = byRepo[key] || []).push(i);
  }

  return (
    <div className="playbook-tab">
      <div className="playbook-intro">
        The playbook is tribal knowledge every agent inherits via CLAUDE.md — tooling tips, repo-state notes, team conventions. Different from the Knowledge Pool (which is coding rules retrieved by agents at task kickoff). Think "things I wish I'd known before starting work on this repo."
      </div>

      <div className="playbook-filter-row">
        <div className="playbook-filter-pills">
          {[
            { key: "active", label: "Active", count: active.length },
            { key: "pending", label: "Pending", count: pending.length },
            { key: "all", label: "All", count: insights.length },
          ].map((p) => (
            <button
              key={p.key}
              className={`playbook-filter-pill ${filter === p.key ? "active" : ""}`}
              onClick={() => setFilter(p.key)}
            >
              {p.label}
              {p.count > 0 && <span className="playbook-filter-count">{p.count}</span>}
            </button>
          ))}
        </div>
        {filter === "pending" && pending.length > 1 && (
          <button
            className="btn btn-sm btn-primary playbook-bulk-approve"
            onClick={approveAllPending}
            disabled={bulking}
            title="Approve every pending insight in one go."
          >
            {bulking ? <Loader size={10} className="spin" /> : <Check size={10} />}
            {bulking ? " Approving…" : ` Approve all (${pending.length})`}
          </button>
        )}
      </div>

      {loading ? (
        <p className="page-empty">Loading…</p>
      ) : Object.keys(byRepo).length === 0 ? (
        <div className="empty-state">
          <div className="empty-title">
            {filter === "pending"
              ? "No pending insights"
              : filter === "active"
                ? "No active insights yet"
                : "Playbook's waiting for notes"}
          </div>
          <div className="empty-sub">
            {filter === "pending"
              ? "Agents drop notes here during the gathering. Run a gather from the campfire above and they'll appear."
              : "Agents jot down tribal knowledge here — \"this service expects a trailing slash,\" \"legal wants session tokens hashed.\" Anything approved gets injected into future agent sessions."}
          </div>
        </div>
      ) : (
        <div className="playbook-groups">
          {Object.entries(byRepo).sort().map(([repo, items]) => (
            <div key={repo} className="playbook-group">
              <div className="playbook-group-header">
                <span className="playbook-group-name">{repo}</span>
                <span className="playbook-group-count">{items.length}</span>
                {repo !== "(global)" && (
                  <button
                    className="btn btn-sm playbook-cartograph-btn"
                    onClick={() => handleCartograph(repo)}
                    disabled={cartographing === repo}
                    title="Spawn Atlas the cartographer to draft a Repo Overview for this repo"
                  >
                    {cartographing === repo
                      ? <><Loader size={11} className="spin" /> Mapping…</>
                      : <><MapIcon size={11} /> Cartograph</>}
                  </button>
                )}
              </div>
              <div className="playbook-group-items">
                {items.map((ins) => {
                  const isOverview = (ins.tags || []).includes("overview");
                  const isEditing = editingId === ins.id;
                  const author = authorLabel(ins.author_agent_id);
                  return (
                    <div
                      key={ins.id}
                      className={`playbook-item ${ins.status} ${ins.expired ? "expired" : ""}`}
                    >
                      <div className="playbook-item-main">
                        {isEditing ? (
                          <textarea
                            className="playbook-edit-input"
                            value={editText}
                            onChange={(e) => setEditText(e.target.value)}
                            rows={isOverview ? 20 : 3}
                            autoFocus
                          />
                        ) : isOverview ? (
                          <div className="playbook-overview">
                            <div className="playbook-overview-header">
                              <button
                                className="playbook-overview-toggle"
                                onClick={() => setExpandedOverviews((s) => ({ ...s, [ins.id]: !s[ins.id] }))}
                              >
                                {expandedOverviews[ins.id]
                                  ? <ChevronDown size={12} />
                                  : <ChevronRight size={12} />}
                                <MapIcon size={12} />
                                <span>Repo Overview</span>
                                <span className="playbook-overview-preview">
                                  {ins.text.split("\n").find((l) => l.trim() && !l.startsWith("#")) || "—"}
                                </span>
                              </button>
                              {repo !== "(global)" && (
                                <button
                                  className="btn btn-sm playbook-overview-refresh"
                                  onClick={(e) => { e.stopPropagation(); handleCartograph(repo); }}
                                  disabled={cartographing === repo}
                                  title={`Re-run Atlas to produce a fresh overview for ${repo}`}
                                >
                                  {cartographing === repo
                                    ? <Loader size={10} className="spin" />
                                    : <RefreshCw size={10} />}
                                </button>
                              )}
                            </div>
                            {expandedOverviews[ins.id] && (
                              <div
                                className="playbook-overview-body"
                                dangerouslySetInnerHTML={{ __html: renderMarkdown(ins.text) }}
                              />
                            )}
                          </div>
                        ) : (
                          <div className="playbook-item-text">{ins.text}</div>
                        )}
                        {!isEditing && (
                          <div className="playbook-item-meta">
                            {ins.status === "pending" && (
                              <span className="playbook-pending-pill">pending</span>
                            )}
                            {ins.expired && <span className="playbook-expired-pill">expired</span>}
                            {author && (
                              <span className="playbook-author">from {author}</span>
                            )}
                            {(ins.tags || [])
                              .filter((t) => t !== "overview" && t !== "cartographer")
                              .map((t) => (
                                <span key={t} className="playbook-tag">{t}</span>
                              ))}
                          </div>
                        )}
                      </div>
                      <div className="playbook-item-actions">
                        {isEditing ? (
                          <>
                            <button className="btn btn-sm btn-primary" onClick={() => saveEdit(ins.id)} title="Save">
                              <Check size={10} />
                            </button>
                            <button className="btn btn-sm" onClick={() => { setEditingId(null); setEditText(""); }} title="Cancel">
                              <X size={10} />
                            </button>
                          </>
                        ) : (
                          <>
                            {ins.status === "pending" && (
                              <button
                                className="btn btn-sm btn-primary playbook-action-primary"
                                onClick={() => approve(ins.id)}
                                title="Approve. Goes into every new agent's CLAUDE.md."
                              >
                                <Check size={10} /> Approve
                              </button>
                            )}
                            {ins.status === "active" && (
                              <button
                                className="playbook-icon-btn"
                                onClick={() => confirm(ins.id)}
                                title="Confirm — bumps freshness, sorts to top"
                              >
                                <RefreshCw size={11} />
                              </button>
                            )}
                            {ins.repo_scope && (
                              <button
                                className="playbook-icon-btn"
                                onClick={() => makeGlobal(ins)}
                                title="Mark global — every agent inherits it, not just agents in this repo."
                              >
                                <span style={{ fontSize: 11 }}>🌐</span>
                              </button>
                            )}
                            <button
                              className="playbook-icon-btn"
                              onClick={() => startEdit(ins)}
                              title="Edit"
                            >
                              <Edit3 size={11} />
                            </button>
                            <button
                              className="playbook-icon-btn playbook-icon-btn-danger"
                              onClick={() => dismiss(ins.id)}
                              title="Dismiss"
                            >
                              <X size={11} />
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="playbook-add-section">
        <button
          className="playbook-add-toggle"
          type="button"
          onClick={() => setAddOpen((v) => !v)}
        >
          {addOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          <Plus size={11} /> Add an insight manually
        </button>
        {addOpen && (
          <div className="playbook-add-form">
            <input
              className="playbook-add-input"
              type="text"
              value={addRepo}
              onChange={(e) => setAddRepo(e.target.value)}
              placeholder="repo (e.g. org/auth-service, blank = global)"
            />
            <input
              className="playbook-add-input"
              type="text"
              value={addTags}
              onChange={(e) => setAddTags(e.target.value)}
              placeholder="tags, comma separated (tooling, migration, team…)"
            />
            <textarea
              className="playbook-add-textarea"
              value={addText}
              onChange={(e) => setAddText(e.target.value)}
              placeholder='e.g. "Use IntelliJ to run tests in this repo, the CLI runner is broken on Windows."'
              rows={2}
            />
            <button
              className="btn btn-primary"
              onClick={handleAdd}
              disabled={adding || !addText.trim()}
            >
              {adding ? <><Loader size={11} className="spin" /> Adding…</> : <><Plus size={11} /> Add</>}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
