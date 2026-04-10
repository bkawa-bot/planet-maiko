import { useState } from "react";
import {
  Bot, Brain, CheckSquare, Plus, Target, TrendingUp, X, Zap,
} from "lucide-react";
import { api } from "../../api/client";
import { showToast } from "../Toast";

const RANK_LABELS = { pup: "🌱 Pup", junior: "⭐ Junior", senior: "🌟 Senior", expert: "👑 Expert" };

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

  if (visibleProfiles.length === 0) {
    return (
      <div className="strategies-view">
        <div className="profiles-toolbar">
          <label className="show-archived-toggle">
            <input type="checkbox" checked={showArchived} onChange={handleToggleArchived} />
            Show archived
          </label>
        </div>
        <div className="empty-state">
          <Target size={36} className="empty-icon" />
          <div className="empty-title">No agent profiles yet</div>
          <div className="empty-sub">Create an agent to start building specialized context sets.</div>
          <button className="btn btn-primary" onClick={onCreateAgent} style={{ marginTop: 12 }}>
            <Plus size={12} /> Create First Agent
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="strategies-view">
      <div className="profiles-toolbar">
        <label className="show-archived-toggle">
          <input type="checkbox" checked={showArchived} onChange={handleToggleArchived} />
          Show archived
        </label>
      </div>

      <div className="strategies-grid">
        {visibleProfiles.map((p) => {
          const specEntries = Object.entries(p.specializations || {}).sort((a, b) => b[1] - a[1]);
          const totalTasks = p.tasks_completed + p.tasks_failed;
          const isPup = totalTasks < 3;
          const strengths = specEntries.filter(([, s]) => s >= 0.7);
          const hasContextSet = p.context_set?.length > 0;

          return (
            <div key={p.id} className={`strategy-card card ${p.archived ? "archived" : ""}`}>
              <div className="strategy-header">
                <div className="strategy-avatar"><Bot size={20} /></div>
                <div className="strategy-identity">
                  <div className="strategy-name">{p.display_name}</div>
                  <div className="strategy-meta">
                    <span className={`strategy-rank rank-${p.rank || "pup"}`}>{RANK_LABELS[p.rank] || "🌱 Pup"}</span>
                    {isPup && <span className="strategy-tag exploring"><Zap size={9} /> exploring</span>}
                    {p.archived && <span className="strategy-tag archived-tag">archived</span>}
                  </div>
                </div>
                <div className="strategy-score-ring">
                  <span className="strategy-score-val">{(p.success_rate * 100).toFixed(0)}%</span>
                  <span className="strategy-score-label">success</span>
                </div>
              </div>

              {p.flavor_text && <div className="strategy-flavor">"{p.flavor_text}"</div>}

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

              <div className="strategy-card-actions">
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
