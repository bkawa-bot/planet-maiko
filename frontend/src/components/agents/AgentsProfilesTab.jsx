import { useEffect, useState } from "react";
import {
  Bot, Brain, CheckSquare, Clock, Plus, Target, TrendingUp, X, Pencil, Save, Code2, Eye, Search, Map, Loader,
} from "lucide-react";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import AgentTimelineModal from "./AgentTimelineModal";

const ROLE_META = {
  coding: { icon: Code2, label: "Coder", color: "var(--pink)" },
  review: { icon: Eye, label: "Reviewer", color: "var(--blue)" },
  investigation: { icon: Search, label: "Investigator", color: "var(--lavender)" },
  cartographer: { icon: Map, label: "Cartographer", color: "var(--lemon)" },
};

// Section order for the role-grouped view.
const ROLE_ORDER = ["coding", "review", "investigation", "cartographer"];


// Small inline control: picks a configured repo and fires Atlas to
// cartograph it. Lives in the Profiles toolbar so there's a durable
// entry point even before any Atlas profile or any insight exists —
// the Playbook's per-repo Cartograph buttons only appear once insights
// exist, which is the chicken-and-egg case a first-time user hits.
function CartographLauncher() {
  const [repos, setRepos] = useState([]);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState("");
  const [spawning, setSpawning] = useState(false);

  useEffect(() => {
    api.getConfig().then((c) => {
      const list = c?.github?.repos || [];
      setRepos(list);
      if (list.length > 0) setSelected(list[0]);
    }).catch(() => {});
  }, []);

  const handleSpawn = async () => {
    if (!selected || spawning) return;
    setSpawning(true);
    try {
      await api.cartographRepo(selected);
      showToast(`Atlas is mapping ${selected} 🗺️`, "normal");
      setOpen(false);
    } catch (err) {
      showToast(err.message || "Couldn't spawn Atlas", "high");
    }
    setSpawning(false);
  };

  if (repos.length === 0) {
    return null;
  }

  return (
    <div className="cartograph-launcher">
      <button
        className="btn btn-sm"
        onClick={() => setOpen((v) => !v)}
        title="Send Atlas to walk a repo and write a Repo Overview"
      >
        <Map size={10} /> Cartograph a repo
      </button>
      {open && (
        <div className="cartograph-launcher-popover">
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            disabled={spawning}
          >
            {repos.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
          <button
            className="btn btn-sm btn-primary"
            onClick={handleSpawn}
            disabled={spawning || !selected}
          >
            {spawning ? <Loader size={10} className="spin" /> : <Map size={10} />}
            {spawning ? " Sending…" : " Send Atlas"}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Profiles tab — agent context-set strategy view.
 *
 * Props:
 *   profiles            — AgentProfile[] from /api/profiles
 *   allLearnings        — { [id]: Learning } map for context set lookups
 *   onCreateAgent       — () => void, opens the create-agent flow in parent
 *   onProfilesChanged   — () => void, called after archive/unarchive so
 *                         the parent can refetch
 *   onShowArchived      — (showArchived: bool) => Promise<Profile[]>, parent
 *                         refetches with the right filter and updates state
 */
export default function AgentsProfilesTab({
  profiles,
  allLearnings,
  onCreateAgent,
  onProfilesChanged,
  onShowArchived,
}) {
  const [showArchived, setShowArchived] = useState(false);
  const [contextSetModal, setContextSetModal] = useState(null);
  const [timelineFor, setTimelineFor] = useState(null);
  const [editing, setEditing] = useState(null);     // profile being edited
  const [editForm, setEditForm] = useState({ role: "coding", scope_repo: "", instructions: "", flavor_text: "" });
  const [editSaving, setEditSaving] = useState(false);

  const openEdit = (p) => {
    setEditing(p);
    setEditForm({
      role: p.role || "coding",
      scope_repo: p.scope_repo || "",
      instructions: p.instructions || "",
      flavor_text: p.flavor_text || "",
    });
  };

  const saveEdit = async () => {
    if (!editing) return;
    setEditSaving(true);
    try {
      await api.updateProfile(editing.id, editForm);
      showToast(`Saved ${editing.display_name}`, "normal");
      setEditing(null);
      onProfilesChanged();
    } catch (err) {
      showToast(err.message || "Save failed", "high");
    }
    setEditSaving(false);
  };

  const handleToggleArchived = async (e) => {
    setShowArchived(e.target.checked);
    await onShowArchived(e.target.checked);
  };

  const handleArchive = async (p) => {
    await api.archiveProfile(p.id);
    showToast(`${p.display_name} archived`, "normal");
    onProfilesChanged();
  };

  const handleUnarchive = async (p) => {
    await api.unarchiveProfile(p.id);
    showToast(`${p.display_name} is back!`, "normal");
    onProfilesChanged();
  };

  const visibleProfiles = showArchived ? profiles : profiles.filter((p) => !p.archived);

  // Group by role for the section view. Profiles without a known role
  // fall into coding (historical default).
  const byRole = {};
  for (const p of visibleProfiles) {
    const r = ROLE_META[p.role] ? p.role : "coding";
    (byRole[r] = byRole[r] || []).push(p);
  }

  if (visibleProfiles.length === 0) {
    return (
      <div className="strategies-view">
        <div className="profiles-toolbar">
          <CartographLauncher />
          <label className="show-archived-toggle">
            <input type="checkbox" checked={showArchived} onChange={handleToggleArchived} />
            Show archived
          </label>
        </div>
        <div className="empty-state">
          <Target size={36} className="empty-icon" />
          <div className="empty-title">The pack is quiet</div>
          <div className="empty-sub">Agents live here — each one specializes in a repo or a kind of work. Welcome the first specialist and they'll get to know your code.</div>
          <button className="btn btn-primary" onClick={onCreateAgent} style={{ marginTop: 12 }}>
            <Plus size={12} /> Welcome an agent
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="strategies-view">
      <div className="profiles-toolbar">
        <CartographLauncher />
        <label className="show-archived-toggle">
          <input type="checkbox" checked={showArchived} onChange={handleToggleArchived} />
          Show archived
        </label>
      </div>

      {ROLE_ORDER.filter((r) => (byRole[r] || []).length > 0).map((role) => {
        const meta = ROLE_META[role];
        const RoleIcon = meta.icon;
        return (
          <div key={role} className="agents-role-section">
            <div className="agents-role-header" style={{ color: meta.color }}>
              <RoleIcon size={14} /> {meta.label}s
              <span className="agents-role-count">{byRole[role].length}</span>
            </div>
            <div className="strategies-grid">
              {byRole[role].map((p) => {
                const specEntries = Object.entries(p.specializations || {}).sort((a, b) => b[1] - a[1]);
                const strengths = specEntries.filter(([, s]) => s >= 0.7);
                const hasContextSet = p.context_set?.length > 0;
                const hasAdapter = !!p.extra?.adapter_path;

                return (
                  <div key={p.id} className={`strategy-card card ${p.archived ? "archived" : ""}`}>
                    <div className="strategy-header">
                      <div className="strategy-avatar"><Bot size={20} /></div>
                      <div className="strategy-identity">
                        <div className="strategy-name">{p.display_name}</div>
                        <div className="strategy-meta">
                          {p.scope_repo && <span className="strategy-tag scope-tag">{p.scope_repo}</span>}
                          {!p.scope_repo && <span className="strategy-tag scope-tag">global</span>}
                          {hasAdapter && <span className="strategy-tag adapter-tag" title="has LoRA adapter">LoRA</span>}
                          {p.archived && <span className="strategy-tag archived-tag">archived</span>}
                        </div>
                      </div>
                    </div>

                    {p.flavor_text && <div className="strategy-flavor">"{p.flavor_text}"</div>}

                    {p.instructions && (
                      <div className="strategy-bio" title={p.instructions}>
                        {p.instructions.length > 260
                          ? p.instructions.slice(0, 257).replace(/\s+\S*$/, "") + "…"
                          : p.instructions}
                      </div>
                    )}

                    {strengths.length > 0 && (
                      <div className="strategy-section">
                        <div className="strategy-section-label"><TrendingUp size={10} /> Strengths</div>
                        <div className="strategy-chips">
                          {strengths.slice(0, 4).map(([key, score]) => (
                            <span key={key} className="strategy-chip strength">
                              {key.split(":").pop()?.replace(/_/g, " ")}{" "}
                              <span className="chip-score">{(score * 100).toFixed(0)}%</span>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="strategy-stats-row">
                      <span className="strategy-stat"><CheckSquare size={10} /> {p.tasks_completed} done</span>
                      <span className="strategy-stat"><X size={10} /> {p.tasks_failed} failed</span>
                      <span
                        className={`strategy-stat ${hasContextSet ? "clickable" : ""}`}
                        onClick={(e) => {
                          if (hasContextSet) {
                            e.stopPropagation();
                            setContextSetModal(p);
                          }
                        }}
                      >
                        <Brain size={10} /> {p.context_set?.length || 0} learnings
                      </span>
                    </div>

                    {p.recent_tasks && p.recent_tasks.length > 0 && (
                      <div className="strategy-recent">
                        <div className="strategy-section-label"><CheckSquare size={10} /> Recent</div>
                        <ul className="strategy-recent-list">
                          {p.recent_tasks.map((rt) => (
                            <li key={rt.id} className="strategy-recent-item" title={rt.title}>
                              {rt.has_artifact && <span className="strategy-recent-dot">•</span>}
                              <span className="strategy-recent-title">{rt.title}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <div className="strategy-card-actions">
                      <button
                        className="btn btn-sm"
                        onClick={() => setTimelineFor({ agentId: p.id, agentName: p.display_name })}
                        title={`See ${p.display_name}'s activity across all tasks`}
                      >
                        <Clock size={10} /> Timeline
                      </button>
                      <button className="btn btn-sm" onClick={() => openEdit(p)}>
                        <Pencil size={10} /> Edit
                      </button>
                      {p.archived ? (
                        <button className="btn btn-sm" onClick={() => handleUnarchive(p)}>Unarchive</button>
                      ) : (
                        <button className="btn btn-sm btn-danger" onClick={() => handleArchive(p)}>
                          <X size={10} /> Archive
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {editing && (
        <div className="modal-overlay" onClick={() => !editSaving && setEditing(null)}>
          <div className="agent-edit-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Pencil size={14} /> Edit {editing.display_name}
              <span style={{ flex: 1 }} />
              <button className="btn btn-sm" onClick={() => setEditing(null)} disabled={editSaving}>
                <X size={12} />
              </button>
            </div>
            <div className="modal-body agent-edit-body">
              <div className="agent-edit-row">
                <label>
                  Role
                  <select
                    value={editForm.role}
                    onChange={(e) => setEditForm({ ...editForm, role: e.target.value })}
                  >
                    <option value="coding">Coder</option>
                    <option value="review">Reviewer</option>
                    <option value="investigation">Investigator</option>
                  </select>
                </label>
                <label>
                  Scope (repo)
                  <input
                    type="text"
                    value={editForm.scope_repo}
                    onChange={(e) => setEditForm({ ...editForm, scope_repo: e.target.value })}
                    placeholder="org/repo  (leave blank for global)"
                  />
                </label>
              </div>
              <label className="agent-edit-full">
                Flavor text
                <input
                  type="text"
                  value={editForm.flavor_text}
                  onChange={(e) => setEditForm({ ...editForm, flavor_text: e.target.value })}
                  placeholder="e.g. Loves debugging. Afraid of CSS."
                />
              </label>
              <label className="agent-edit-full">
                Instructions (markdown, injected into every session)
                <textarea
                  rows={10}
                  value={editForm.instructions}
                  onChange={(e) => setEditForm({ ...editForm, instructions: e.target.value })}
                  placeholder={`## Your style
- Write tests first
- Prefer standard library over dependencies
- Keep functions under 50 lines

## Patterns to watch for in this repo
- Always use the existing logger, not print()
- ...`}
                />
              </label>
            </div>
            <div className="agent-edit-footer">
              <button className="btn" onClick={() => setEditing(null)} disabled={editSaving}>Cancel</button>
              <button className="btn btn-primary" onClick={saveEdit} disabled={editSaving}>
                <Save size={12} /> {editSaving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

      {timelineFor && (
        <AgentTimelineModal
          agentId={timelineFor.agentId}
          agentName={timelineFor.agentName}
          onClose={() => setTimelineFor(null)}
        />
      )}

      {contextSetModal && (
        <div className="modal-overlay" onClick={() => setContextSetModal(null)}>
          <div className="info-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Brain size={16} /> {contextSetModal.display_name}'s Context Set
              <span style={{ flex: 1 }} />
              <button className="btn btn-sm modal-close-btn" onClick={() => setContextSetModal(null)}>
                <X size={14} />
              </button>
            </div>
            <div className="modal-body">
              <div className="context-set-list">
                {(contextSetModal.context_set || []).map((lid) => {
                  const l = allLearnings[lid];
                  return (
                    <div key={lid} className="context-set-item">
                      <span className="context-set-cat">{l?.category?.replace(/_/g, " ") || "unknown"}</span>
                      <span className="context-set-rule">{l?.rule || `Learning #${lid}`}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
