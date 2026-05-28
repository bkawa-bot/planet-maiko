import { useEffect, useState } from "react";
import {
  Brain, CheckSquare, ChevronDown, ChevronRight, Plus,
  Target, X, Pencil, Save, Map, Loader, Compass, Pause, Play,
} from "@icons";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import { formatRepo, useDefaultOrg, useConfiguredRepos } from "../../utils/repo";
import { relativeTime } from "../../utils/dates";
import CardAvatar from "../CardAvatar";
import CardArt from "../CardArt";
import ProfileDetailModal from "./ProfileDetailModal";
import ModalPortal from "../ModalPortal";
import { useAgentTypes, roleMeta } from "../../hooks/useAgentTypes";

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
// Surfaces only the agent's personal tagline (profile.flavor_text) -
// the archetype tagline (card.tagline) is the same line for every
// Wandering Fox in town and adds noise here; it lives in the modal
// where the user actually wants the type-level context. Stats
// (done count, age) also live in the modal so the card stays focused
// on identity at a glance.
function ProfileCard({ profile, onOpen }) {
  const defaultOrg = useDefaultOrg();
  const personalTagline = (profile.flavor_text || "").trim();
  return (
    <button
      type="button"
      className={`profile-card-mini ${profile.archived ? "archived" : ""}`}
      onClick={onOpen}
    >
      <div className="profile-card-avatar"><CardAvatar agent={profile} size="lg" /></div>
      <div className="profile-card-body">
        <div className="profile-card-name">
          <span
            className={`agent-state-dot state-${profile.state || "idle"}`}
            title={`Agent state: ${profile.state || "idle"}`}
          />
          <span className="profile-card-name-text">{profile.display_name}</span>
        </div>
        {profile.scope_repo && (
          <div className="profile-card-chips">
            <span className="profile-card-chip" title={profile.scope_repo}>{formatRepo(profile.scope_repo, defaultOrg)}</span>
          </div>
        )}
        {personalTagline && (
          <div className="profile-card-preview">
            {personalTagline.length > 110 ? personalTagline.slice(0, 108).replace(/\s+\S*$/, "") + "…" : personalTagline}
          </div>
        )}
      </div>
    </button>
  );
}


// Days since the profile's created_at, floored. Returns null when
// the timestamp is missing or unparseable so the card doesn't render
// "NaNd old". Same locale-relaxed parsing as everywhere else — ISO
// strings from the API plus tz markers handle correctly.
function ageInDays(createdAt) {
  if (!createdAt) return null;
  const created = new Date(createdAt);
  if (isNaN(created.getTime())) return null;
  const ms = Date.now() - created.getTime();
  return Math.max(0, Math.floor(ms / 86400000));
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
  const [profileModal, setProfileModal] = useState(null);
  const [editing, setEditing] = useState(null);
  const [editForm, setEditForm] = useState({ role: "coding", scope_repo: "", instructions: "", flavor_text: "", specialty_ids: [] });
  const [editSaving, setEditSaving] = useState(false);
  const [specialties, setSpecialties] = useState([]);
  const agentTypes = useAgentTypes();
  const configuredRepos = useConfiguredRepos();

  useEffect(() => {
    // Pull available specialties once — the edit modal renders a chip
    // grid so the user can attach / detach without leaving the agents
    // page. Specialty authoring still lives on the Specialties tab.
    api.getSkills()
      .then((list) => setSpecialties(Array.isArray(list) ? list : []))
      .catch(() => setSpecialties([]));
  }, []);

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
      specialty_ids: Array.isArray(p.specialty_ids) ? [...p.specialty_ids] : [],
    });
  };

  const toggleEditSpecialty = (id) => {
    setEditForm((prev) => {
      const has = prev.specialty_ids.includes(id);
      return {
        ...prev,
        specialty_ids: has
          ? prev.specialty_ids.filter((x) => x !== id)
          : [...prev.specialty_ids, id],
      };
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

  // Role-bucket order comes from the AgentTypes API (defaults first,
  // then custom types alpha). Profiles whose role doesn't match any
  // AgentType fall back into the "coding" bucket — keeps stale-role
  // profiles visible instead of silently dropping them.
  const knownRoles = new Set(agentTypes.map((t) => t.id));
  const ROLE_ORDER = agentTypes.map((t) => t.id);
  const byRole = {};
  for (const p of visibleProfiles) {
    const r = knownRoles.has(p.role) ? p.role : "coding";
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
        const meta = roleMeta(role, agentTypes);
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
          specialties={specialties}
          onClose={() => setProfileModal(null)}
          onEdit={(p) => { setProfileModal(null); openEdit(p); }}
          onArchive={(p) => { handleArchive(p); setProfileModal(null); }}
          onUnarchive={(p) => { handleUnarchive(p); setProfileModal(null); }}
        />
      )}

      {editing && (
        <ModalPortal>
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
                    <option value="cartographer">Cartographer</option>
                  </select>
                </label>
                <label>
                  Scope (repo)
                  <input
                    type="text"
                    value={editForm.scope_repo}
                    onChange={(e) => setEditForm({ ...editForm, scope_repo: e.target.value })}
                    placeholder="org/repo  (leave blank for global)"
                    list={configuredRepos.length ? "agents-edit-profile-repos" : undefined}
                  />
                  {configuredRepos.length > 0 && (
                    <datalist id="agents-edit-profile-repos">
                      {configuredRepos.map((r) => <option key={r} value={r} />)}
                    </datalist>
                  )}
                </label>
              </div>
              {specialties.length > 0 && (
                <div className="agent-edit-full">
                  {/* NOT a <label>: a label wrapping multiple buttons
                      auto-dispatches a click to the first one when the
                      label area is clicked, silently selecting the
                      first specialty without the user noticing. */}
                  <div className="agent-edit-label">Specialties</div>
                  <div className="agent-specialty-grid">
                    {specialties.map((s) => {
                      const checked = editForm.specialty_ids.includes(s.id);
                      return (
                        <button
                          type="button"
                          key={s.id}
                          className={`agent-specialty-chip ${checked ? "checked" : ""}`}
                          onClick={() => toggleEditSpecialty(s.id)}
                          title={s.description || s.name}
                        >
                          {s.name}
                        </button>
                      );
                    })}
                  </div>
                  <span className="agent-edit-hint">
                    Extra context a run can layer on top of the role. A run with no specialty picked uses the base role protocol.
                  </span>
                </div>
              )}
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
        </ModalPortal>
      )}

    </div>
  );
}
