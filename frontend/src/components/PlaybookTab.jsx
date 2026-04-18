import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { renderMarkdown } from "../utils/markdown";
import {
  Check, X, Edit3, Plus, RefreshCw, Clock, Loader, Map as MapIcon,
  ChevronDown, ChevronRight,
} from "lucide-react";
import "./PlaybookTab.css";

/**
 * Playbook tab: tribal / operational knowledge scoped per repo.
 *
 * Different intent from Learnings. Learnings are coding rules that
 * feed the LoRA; Insights are workflow context injected verbatim
 * into every new agent's CLAUDE.md — tooling quirks, repo state,
 * team conventions.
 *
 * Surfaces:
 *   - Pending queue (agent-authored, awaiting user approval)
 *   - Active playbook grouped by repo (confirm / edit / dismiss)
 *   - Manual "add a note" form
 */
export default function PlaybookTab({ onCountsChange }) {
  const [insights, setInsights] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("active"); // active | pending | all
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState("");
  const [addText, setAddText] = useState("");
  const [addRepo, setAddRepo] = useState("");
  const [addTags, setAddTags] = useState("");
  const [adding, setAdding] = useState(false);
  const [cartographing, setCartographing] = useState(null); // repo name while spawn in flight
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
    showToast("Confirmed — moved to top", "normal");
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

  const handleCartograph = async (repo) => {
    if (!repo || repo === "(global)" || cartographing) return;
    setCartographing(repo);
    try {
      const result = await api.cartographRepo(repo);
      showToast(`${result.profile_name || "Atlas"} is walking ${repo} — check the Pending queue in a few minutes`, "normal");
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
      // Keep the repo so the user can add several in a row without retyping.
      showToast("Insight added", "normal");
      fetchAll();
    } catch (err) {
      showToast(err.message || "Couldn't add insight", "high");
    }
    setAdding(false);
  };

  // Partition by status + group active by repo_scope.
  const pending = insights.filter((i) => i.status === "pending");
  const active = insights.filter((i) => i.status === "active");
  const visible = filter === "pending" ? pending
    : filter === "active" ? active
    : insights;

  const byRepo = {};
  for (const i of visible) {
    const key = i.repo_scope || "(global)";
    (byRepo[key] = byRepo[key] || []).push(i);
  }

  return (
    <div className="playbook-tab">
      <div className="playbook-intro">
        The playbook is tribal knowledge every agent inherits via CLAUDE.md — tooling tips, repo-state notes, team conventions. Different from the Knowledge Pool (which is coding rules feeding the LoRA). Think "things I wish I'd known before starting work on this repo."
      </div>

      <div className="playbook-filter-row">
        <div className="playbook-filter-pills">
          {["active", "pending", "all"].map((f) => (
            <button
              key={f}
              className={`playbook-filter-pill ${filter === f ? "active" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f === "pending" && pending.length > 0 ? `Pending (${pending.length})` : f}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="page-empty">Loading…</p>
      ) : Object.keys(byRepo).length === 0 ? (
        <div className="empty-state">
          <div className="empty-title">Playbook's waiting for notes</div>
          <div className="empty-sub">
            Agents jot down tribal knowledge here — "this service expects a trailing slash," "legal wants session tokens hashed," that kind of thing. Anything added gets injected into future agent sessions. Add one yourself below if you already have something worth remembering.
          </div>
        </div>
      ) : (
        <div className="playbook-groups">
          {Object.entries(byRepo).sort().map(([repo, items]) => (
            <div key={repo} className="playbook-group">
              <div className="playbook-group-header">
                <span>{repo}</span>
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
                {items.map((ins) => (
                  <div
                    key={ins.id}
                    className={`playbook-item ${ins.status} ${ins.expired ? "expired" : ""}`}
                  >
                    <div className="playbook-item-main">
                      {editingId === ins.id ? (
                        <textarea
                          className="playbook-edit-input"
                          value={editText}
                          onChange={(e) => setEditText(e.target.value)}
                          rows={(ins.tags || []).includes("overview") ? 20 : 3}
                          autoFocus
                        />
                      ) : (ins.tags || []).includes("overview") ? (
                        <div className="playbook-overview">
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
                      <div className="playbook-item-meta">
                        {ins.status === "pending" && (
                          <span className="badge paused">pending</span>
                        )}
                        {ins.expired && <span className="badge">expired</span>}
                        {ins.author_agent_id && (
                          <span className="tag">
                            from {ins.author_agent_id.replace(/^agent-/, "")}
                          </span>
                        )}
                        {(ins.tags || []).map((t) => (
                          <span key={t} className="tag">{t}</span>
                        ))}
                      </div>
                    </div>
                    <div className="playbook-item-actions">
                      {editingId === ins.id ? (
                        <>
                          <button className="btn btn-sm btn-primary" onClick={() => saveEdit(ins.id)}>
                            <Check size={10} />
                          </button>
                          <button className="btn btn-sm" onClick={() => { setEditingId(null); setEditText(""); }}>
                            <X size={10} />
                          </button>
                        </>
                      ) : (
                        <>
                          {ins.status === "pending" && (
                            <button className="btn btn-sm btn-approve" onClick={() => approve(ins.id)} title="Approve — goes into every new agent's CLAUDE.md">
                              <Check size={10} /> Approve
                            </button>
                          )}
                          {ins.status === "active" && (
                            <button
                              className="btn btn-sm"
                              onClick={() => confirm(ins.id)}
                              title="Confirm — bumps freshness, sorts to top"
                            >
                              <RefreshCw size={10} />
                            </button>
                          )}
                          <button className="btn btn-sm" onClick={() => startEdit(ins)} title="Edit">
                            <Edit3 size={10} />
                          </button>
                          <button className="btn btn-sm btn-danger" onClick={() => dismiss(ins.id)} title="Dismiss">
                            <X size={10} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="playbook-add-section">
        <div className="playbook-add-header">
          <Plus size={12} /> Add an insight
        </div>
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
            placeholder='e.g. "Use IntelliJ to run tests in this repo — the CLI runner is broken on Windows."'
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
      </div>
    </div>
  );
}
