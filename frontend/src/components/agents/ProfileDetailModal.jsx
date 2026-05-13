import { useState } from "react";
import {
  Brain, CheckSquare, X, Pencil, Code2, Eye, Search, Map, Sparkles,
} from "lucide-react";
import { formatRepo, useDefaultOrg } from "../../utils/repo";
import CardArt from "../CardArt";
import RarityBadge from "../RarityBadge";
import { useCards } from "../../hooks/useCards";

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




// User-facing strings for automation metadata. Small helpers shared
// Full profile modal — shown when a card is clicked.
export default function ProfileDetailModal({
  profile, allLearnings, specialties, onClose, onEdit, onArchive, onUnarchive,
}) {
  const [showContextSet, setShowContextSet] = useState(false);
  const defaultOrg = useDefaultOrg();
  const hasContextSet = profile.context_set?.length > 0;
  const role = profile.role || "coding";
  const meta = ROLE_META[role] || ROLE_META.coding;
  const RoleIcon = meta.icon;
  const cards = useCards();
  const card = cards.find((c) => c.id === profile.avatar);
  // Days since created_at, floored. Null if missing or unparseable so
  // the stat just doesn't render rather than showing NaN.
  const ageDays = (() => {
    if (!profile.created_at) return null;
    const created = new Date(profile.created_at);
    const ms = Date.now() - created.getTime();
    if (Number.isNaN(ms) || ms < 0) return null;
    return Math.floor(ms / (24 * 3600 * 1000));
  })();

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="profile-detail-modal" onClick={(e) => e.stopPropagation()}>
        <button className="btn btn-sm modal-close-btn profile-modal-close-floating" onClick={onClose}>
          <X size={14} />
        </button>

        <div className="profile-modal-split">
          {/* Left column: archetype card art + name/tagline. The
              archetype tagline lives here only — the mini ProfileCard
              dropped it because every Wandering Fox shares the same
              line and it added noise on the grid. */}
          <div className="profile-modal-left">
            <CardArt cardId={profile.avatar} className="profile-modal-card-art" />
            {card && (
              <div className="profile-modal-archetype">
                <div style={{ marginBottom: 6 }}>
                  <RarityBadge rarity={card.rarity} />
                </div>
                <div className="profile-modal-archetype-name">
                  <span style={{ opacity: 0.55, fontWeight: 500, marginRight: 4 }}>Type:</span>
                  {card.display_name}
                </div>
                <div className="profile-modal-archetype-tagline">{card.tagline}</div>
              </div>
            )}
          </div>

          {/* Right column: identity + agent's own voice + stats +
              specialties + sections. Scrolls independently when
              the agent's bio / context set / automations push past
              the modal height. */}
          <div className="profile-modal-right">
            <div className="profile-modal-identity">
              <div className="profile-modal-name">
                <span className={`agent-state-dot state-${profile.state || "idle"}`} />
                {profile.display_name}
              </div>
              <div className="profile-modal-meta">
                <span className="profile-modal-role" style={{ color: meta.color }}>
                  <RoleIcon size={10} /> {meta.label}
                </span>
                <span className="profile-modal-chip" title={profile.scope_repo || ""}>{profile.scope_repo ? formatRepo(profile.scope_repo, defaultOrg) : "global"}</span>
                {profile.archived && <span className="profile-modal-chip profile-modal-chip-archived">archived</span>}
              </div>
            </div>

            {profile.flavor_text && (
              <div className="profile-modal-flavor">"{profile.flavor_text}"</div>
            )}

            {profile.instructions && (
              <div className="profile-modal-bio">
                {profile.instructions}
              </div>
            )}

            {/* Stats row — `done` and `failed` increment from the brain
                cycle and outbox handler; `learnings` here is the size
                of context_set (the rules the agent has graduated to
                exploit). The legacy `learnings_contributed` model
                field isn't shown — it's a stub that's never
                incremented anywhere, so rendering it as 0 was
                misleading. */}
            <div className="profile-modal-stats">
              <div className="profile-modal-stat">
                <div className="profile-modal-stat-value">{profile.tasks_completed}</div>
                <div className="profile-modal-stat-label">done</div>
              </div>
              {ageDays != null && (
                <div
                  className="profile-modal-stat"
                  title={`Joined ${profile.created_at}`}
                >
                  <div className="profile-modal-stat-value">{ageDays}</div>
                  <div className="profile-modal-stat-label">
                    {ageDays === 0 ? "new today" : ageDays === 1 ? "day old" : "days old"}
                  </div>
                </div>
              )}
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

            {(profile.specialty_ids || []).length > 0 && (
              <div className="profile-modal-specialties">
                <div className="profile-modal-section-label">
                  <Sparkles size={11} /> Specialties
                </div>
                <div className="profile-modal-specialty-chips">
                  {(profile.specialty_ids || []).map((sid) => {
                    const s = (specialties || []).find((x) => x.id === sid);
                    return (
                      <span
                        key={sid}
                        className="profile-modal-specialty-chip"
                        title={s?.description || sid}
                      >
                        {s?.name || sid}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

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

          </div>
        </div>

        <div className="profile-modal-footer">
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