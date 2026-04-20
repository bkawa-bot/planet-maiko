import { useEffect, useState } from "react";
import {
  Bot, Brain, CheckSquare, ChevronDown, ChevronRight, Clock, Plus,
  Target, X, Pencil, Save, Code2, Eye, Search, Map, Loader, Compass, Pause, Play,
} from "lucide-react";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import { formatRepo, useDefaultOrg } from "../../utils/repo";
import { relativeTime } from "../../utils/dates";
import AgentTimelineModal from "./AgentTimelineModal";

const ROLE_META = {
  coding: { icon: Code2, label: "Coder", color: "var(--pink)" },
  review: { icon: Eye, label: "Reviewer", color: "var(--blue)" },
  investigation: { icon: Search, label: "Investigator", color: "var(--lavender)" },
  cartographer: { icon: Map, label: "Cartographer", color: "var(--lemon)" },
};

// Section order for the role-grouped view.
const ROLE_ORDER = ["coding", "review", "investigation", "cartographer"];

// Auto-collapse a role section when it has more than this many agents.
// Keeps the pack-grows-large case scannable without forcing users with
// 3 agents to click every header.
const AUTO_COLLAPSE_THRESHOLD = 6;

const COLLAPSE_STORAGE_KEY = "maiko-profiles-collapsed";


function CartographLauncher() {
  const [repos, setRepos] = useState([]);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState("");
  const [spawning, setSpawning] = useState(false);
  const defaultOrg = useDefaultOrg();

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

  if (repos.length === 0) return null;

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
              <option key={r} value={r}>{formatRepo(r, defaultOrg)}</option>
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


// Compact card. Clicking anywhere opens the full profile modal.
function ProfileCard({ profile, onOpen }) {
  const preview = (profile.instructions || profile.flavor_text || "").trim();
  const defaultOrg = useDefaultOrg();
  return (
    <button
      type="button"
      className={`profile-card-mini ${profile.archived ? "archived" : ""}`}
      onClick={onOpen}
    >
      <div className="profile-card-avatar"><Bot size={16} /></div>
      <div className="profile-card-body">
        <div className="profile-card-name">
          <span
            className={`agent-state-dot state-${profile.state || "idle"}`}
            title={`Agent state: ${profile.state || "idle"}`}
          />
          <span className="profile-card-name-text">{profile.display_name}</span>
          {profile.scope_repo && (
            <span className="profile-card-chip" title={profile.scope_repo}>{formatRepo(profile.scope_repo, defaultOrg)}</span>
          )}
          {profile.extra?.adapter_path && (
            <span className="profile-card-chip profile-card-chip-lora">LoRA</span>
          )}
        </div>
        {preview && (
          <div className="profile-card-preview">
            {preview.length > 110 ? preview.slice(0, 108).replace(/\s+\S*$/, "") + "…" : preview}
          </div>
        )}
      </div>
    </button>
  );
}


// User-facing strings for goal metadata. Both functions are pure —
// they only depend on the goal row, so they stay outside the component
// to keep React from rebuilding them on every render.
function formatGoalKind(kind) {
  const map = {
    keep_overview_current: "Keep overview current",
    train_lora_when_ready: "Train LoRA when rules accumulate",
  };
  return map[kind] || kind.replace(/_/g, " ");
}

function goalTriggerDescription(goal) {
  const cfg = goal.trigger_config || {};
  if (goal.kind === "keep_overview_current") {
    const days = cfg.stale_days || 30;
    return `refreshes after ${days}d stale`;
  }
  if (goal.kind === "train_lora_when_ready") {
    const min = cfg.min_learnings || 10;
    return `nudges at ${min}+ rules, no adapter`;
  }
  if (goal.trigger_kind === "cadence" && cfg.cadence_hours) {
    return `every ${cfg.cadence_hours}h`;
  }
  return goal.trigger_kind;
}


// Full profile modal — shown when a card is clicked.
function ProfileDetailModal({
  profile, allLearnings, onClose, onEdit, onTimeline, onArchive, onUnarchive,
}) {
  const [showContextSet, setShowContextSet] = useState(false);
  const [goals, setGoals] = useState(null);
  const [goalsLoading, setGoalsLoading] = useState(true);
  const defaultOrg = useDefaultOrg();
  const hasContextSet = profile.context_set?.length > 0;
  const hasAdapter = !!profile.extra?.adapter_path;
  const role = profile.role || "coding";
  const meta = ROLE_META[role] || ROLE_META.coding;
  const RoleIcon = meta.icon;

  useEffect(() => {
    let cancelled = false;
    setGoalsLoading(true);
    api.getAgentGoals(profile.id)
      .then((rows) => { if (!cancelled) setGoals(rows || []); })
      .catch(() => { if (!cancelled) setGoals([]); })
      .finally(() => { if (!cancelled) setGoalsLoading(false); });
    return () => { cancelled = true; };
  }, [profile.id]);

  const toggleGoal = async (goal) => {
    const nextStatus = goal.status === "active" ? "paused" : "active";
    // Optimistic update — revert on failure so the toggle feels instant
    // but stays honest if the PATCH 500s.
    setGoals((rows) => rows.map((g) => g.id === goal.id ? { ...g, status: nextStatus } : g));
    try {
      const updated = await api.updateGoal(goal.id, { status: nextStatus });
      setGoals((rows) => rows.map((g) => g.id === goal.id ? updated : g));
    } catch (err) {
      setGoals((rows) => rows.map((g) => g.id === goal.id ? { ...g, status: goal.status } : g));
      showToast("Couldn't update goal: " + (err.message || "unknown"), "high");
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="profile-detail-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="profile-modal-avatar"><Bot size={22} /></div>
          <div className="profile-modal-title">
            <div className="profile-modal-name">
              <span className={`agent-state-dot state-${profile.state || "idle"}`} />
              {profile.display_name}
            </div>
            <div className="profile-modal-meta">
              <span className="profile-modal-role" style={{ color: meta.color }}>
                <RoleIcon size={10} /> {meta.label}
              </span>
              <span className="profile-modal-chip" title={profile.scope_repo || ""}>{profile.scope_repo ? formatRepo(profile.scope_repo, defaultOrg) : "global"}</span>
              {hasAdapter && <span className="profile-modal-chip profile-modal-chip-lora">LoRA</span>}
              {profile.archived && <span className="profile-modal-chip profile-modal-chip-archived">archived</span>}
            </div>
          </div>
          <button className="btn btn-sm modal-close-btn" onClick={onClose}>
            <X size={14} />
          </button>
        </div>

        <div className="modal-body profile-modal-body">
          {profile.flavor_text && (
            <div className="profile-modal-flavor">"{profile.flavor_text}"</div>
          )}

          {profile.instructions && (
            <div className="profile-modal-bio">
              {profile.instructions}
            </div>
          )}

          <div className="profile-modal-stats">
            <div className="profile-modal-stat">
              <div className="profile-modal-stat-value">{profile.tasks_completed}</div>
              <div className="profile-modal-stat-label">done</div>
            </div>
            <div className="profile-modal-stat">
              <div className="profile-modal-stat-value">{profile.tasks_failed}</div>
              <div className="profile-modal-stat-label">failed</div>
            </div>
            <button
              type="button"
              className={`profile-modal-stat ${hasContextSet ? "clickable" : ""}`}
              onClick={() => hasContextSet && setShowContextSet((v) => !v)}
              disabled={!hasContextSet}
            >
              <div className="profile-modal-stat-value">{profile.context_set?.length || 0}</div>
              <div className="profile-modal-stat-label">learnings</div>
            </button>
          </div>

          {showContextSet && hasContextSet && (
            <div className="profile-modal-section">
              <div className="profile-modal-section-label">
                <Brain size={11} /> Context set
              </div>
              <div className="context-set-list">
                {(profile.context_set || []).map((lid) => {
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
          )}

          {profile.recent_tasks && profile.recent_tasks.length > 0 && (
            <div className="profile-modal-section">
              <div className="profile-modal-section-label">
                <CheckSquare size={11} /> Recent tasks
              </div>
              <ul className="profile-modal-task-list">
                {profile.recent_tasks.map((rt) => (
                  <li key={rt.id} className="profile-modal-task" title={rt.title}>
                    {rt.has_artifact && <span className="profile-modal-task-dot">•</span>}
                    {rt.title}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="profile-modal-section">
            <div className="profile-modal-section-label">
              <Compass size={11} /> Standing goals
              {goals && goals.length > 0 && (
                <span className="profile-modal-goal-count">{goals.filter((g) => g.status === "active").length} active</span>
              )}
            </div>
            {goalsLoading ? (
              <div className="profile-modal-goals-empty">
                <Loader size={10} className="spin" /> Loading…
              </div>
            ) : !goals || goals.length === 0 ? (
              <div className="profile-modal-goals-empty">
                No standing goals yet. {role === "cartographer" ? "Atlas picks up repo-overview goals automatically once repos are configured." : "More kinds are on the way."}
              </div>
            ) : (
              <ul className="profile-modal-goal-list">
                {goals.map((g) => (
                  <li key={g.id} className={`profile-modal-goal status-${g.status}`}>
                    <div className="profile-modal-goal-main">
                      <span className="profile-modal-goal-kind">{formatGoalKind(g.kind)}</span>
                      {g.scope_repo && (
                        <span className="profile-modal-goal-repo" title={g.scope_repo}>
                          {formatRepo(g.scope_repo, defaultOrg)}
                        </span>
                      )}
                      <span className="profile-modal-goal-meta">
                        {goalTriggerDescription(g)}
                        {g.last_fired_at && (
                          <> · fired {relativeTime(g.last_fired_at)}</>
                        )}
                      </span>
                    </div>
                    <button
                      className="btn btn-sm profile-modal-goal-toggle"
                      onClick={() => toggleGoal(g)}
                      title={g.status === "active" ? "Pause this goal" : "Resume this goal"}
                      disabled={g.status === "archived"}
                    >
                      {g.status === "active" ? <Pause size={10} /> : <Play size={10} />}
                      {g.status === "active" ? " pause" : g.status === "paused" ? " resume" : " archived"}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="profile-modal-footer">
          <button className="btn btn-sm" onClick={() => onTimeline(profile)}>
            <Clock size={10} /> Timeline
          </button>
          <button className="btn btn-sm" onClick={() => onEdit(profile)}>
            <Pencil size={10} /> Edit
          </button>
          <span style={{ flex: 1 }} />
          {profile.archived ? (
            <button className="btn btn-sm" onClick={() => onUnarchive(profile)}>Unarchive</button>
          ) : (
            <button className="btn btn-sm btn-danger" onClick={() => onArchive(profile)}>
              <X size={10} /> Archive
            </button>
          )}
        </div>
      </div>
    </div>
  );
}


/**
 * Profiles tab — agent roster with collapsible role sections and
 * minimal cards. Click a card to open the full profile modal.
 */
export default function AgentsProfilesTab({
  profiles,
  allLearnings,
  onCreateAgent,
  onProfilesChanged,
  onShowArchived,
}) {
  const [showArchived, setShowArchived] = useState(false);
  const [timelineFor, setTimelineFor] = useState(null);
  const [profileModal, setProfileModal] = useState(null);
  const [editing, setEditing] = useState(null);
  const [editForm, setEditForm] = useState({ role: "coding", scope_repo: "", instructions: "", flavor_text: "" });
  const [editSaving, setEditSaving] = useState(false);

  // Collapsed role-section state — persisted to localStorage so the
  // layout sticks across visits. When nothing's persisted, sections
  // auto-collapse above a threshold so a 30+ agent pack opens scannable.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const stored = localStorage.getItem(COLLAPSE_STORAGE_KEY);
      if (stored) return new Set(JSON.parse(stored));
    } catch { /* non-fatal */ }
    return null;  // null = compute default from profile count
  });

  const toggleCollapsed = (role) => {
    setCollapsed((prev) => {
      const next = new Set(prev || []);
      if (next.has(role)) next.delete(role);
      else next.add(role);
      try {
        localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify([...next]));
      } catch { /* quota / private mode */ }
      return next;
    });
  };

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

  const byRole = {};
  for (const p of visibleProfiles) {
    const r = ROLE_META[p.role] ? p.role : "coding";
    (byRole[r] = byRole[r] || []).push(p);
  }

  const isCollapsed = (role) => {
    if (collapsed !== null) return collapsed.has(role);
    // No persisted state yet — auto-collapse large groups.
    return (byRole[role] || []).length > AUTO_COLLAPSE_THRESHOLD;
  };

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
          <div className="empty-sub">Agents live here. Each specializes in a repo or a kind of work. Welcome the first one and they'll get to know your code.</div>
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
        const collapsedNow = isCollapsed(role);
        const ToggleIcon = collapsedNow ? ChevronRight : ChevronDown;
        return (
          <div key={role} className={`agents-role-section ${collapsedNow ? "collapsed" : ""}`}>
            <button
              type="button"
              className="agents-role-header"
              style={{ color: meta.color }}
              onClick={() => toggleCollapsed(role)}
            >
              <ToggleIcon size={12} className="agents-role-chevron" />
              <RoleIcon size={14} /> {meta.label}s
              <span className="agents-role-count">{byRole[role].length}</span>
            </button>
            {!collapsedNow && (
              <div className="profiles-mini-grid">
                {byRole[role].map((p) => (
                  <ProfileCard
                    key={p.id}
                    profile={p}
                    onOpen={() => setProfileModal(p)}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}

      {profileModal && (
        <ProfileDetailModal
          profile={profileModal}
          allLearnings={allLearnings}
          onClose={() => setProfileModal(null)}
          onEdit={(p) => { setProfileModal(null); openEdit(p); }}
          onTimeline={(p) => { setProfileModal(null); setTimelineFor({ agentId: p.id, agentName: p.display_name }); }}
          onArchive={(p) => { handleArchive(p); setProfileModal(null); }}
          onUnarchive={(p) => { handleUnarchive(p); setProfileModal(null); }}
        />
      )}

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
                  placeholder={`## Your style\n- Write tests first\n- Prefer standard library over dependencies\n- Keep functions under 50 lines`}
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
    </div>
  );
}
