import { useEffect, useState } from "react";
import {
  Brain, CheckSquare, ChevronDown, ChevronRight, Plus,
  Target, X, Pencil, Save, Code2, Eye, Search, Map, Loader, Compass, Pause, Play,
} from "lucide-react";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import { formatRepo, useDefaultOrg, useConfiguredRepos } from "../../utils/repo";
import { relativeTime } from "../../utils/dates";
import CardAvatar from "../CardAvatar";
import CardArt from "../CardArt";
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
// across the profile modal, Automations page, and ProposalCard.
// Keep in sync with utils/automations.js style — eventually extract if
// a fourth consumer shows up.
function describeCondition(trigger) {
  const cfg = trigger?.config || {};
  switch (trigger?.kind) {
    case "cadence": {
      if (cfg.interval_minutes) {
        const m = cfg.interval_minutes;
        if (m < 60) return `every ${m}min`;
        if (m % 60 === 0) return `every ${m / 60}h`;
        return `every ${Math.floor(m / 60)}h ${m % 60}min`;
      }
      return `every ${cfg.interval_hours || 24}h`;
    }
    case "overview_stale":
      return `overview >${cfg.stale_days || 30}d stale`;
    case "lora_missing":
      return `${cfg.min_learnings || 10}+ rules, no adapter`;
    default:
      return trigger?.kind || "unknown";
  }
}

function describeAction(action) {
  const cfg = action?.config || {};
  switch (action?.kind) {
    case "propose":
      return `propose: ${cfg.draft?.title || "(no title)"}`;
    case "nudge":
      return `nudge: ${cfg.title || "(no title)"}`;
    case "create_task":
      return `create task: ${cfg.title || "(no title)"}`;
    case "run_skill":
      return `run skill: ${cfg.skill_name || "(none)"}`;
    default:
      return action?.kind || "unknown";
  }
}

function describeAutomationTrigger(automation) {
  const when = automation.when || [];
  if (when.length === 0) return "no trigger";
  if (when.length === 1) return describeCondition(when[0]);
  const logic = automation.when_logic === "any" ? " OR " : " AND ";
  return when.map(describeCondition).join(logic);
}



// Full profile modal — shown when a card is clicked.
export default function ProfileDetailModal({
  profile, allLearnings, onClose, onEdit, onArchive, onUnarchive,
}) {
  const [showContextSet, setShowContextSet] = useState(false);
  const [automations, setAutomations] = useState(null);
  const [automationsLoading, setAutomationsLoading] = useState(true);
  const defaultOrg = useDefaultOrg();
  const hasContextSet = profile.context_set?.length > 0;
  const hasAdapter = !!profile.extra?.adapter_path;
  const role = profile.role || "coding";
  const meta = ROLE_META[role] || ROLE_META.coding;
  const RoleIcon = meta.icon;
  const cards = useCards();
  const card = cards.find((c) => c.id === profile.avatar);

  // Profile card's Automations section shows rows tied to this profile
  // (agent_profile_id matches) plus role-wide ones (agent_profile_id null).
  // Backend list endpoint filters on agent_profile_id=<id> to include both.
  useEffect(() => {
    let cancelled = false;
    setAutomationsLoading(true);
    api.getAutomations({ agent_profile_id: profile.id })
      .then((rows) => { if (!cancelled) setAutomations(rows || []); })
      .catch(() => { if (!cancelled) setAutomations([]); })
      .finally(() => { if (!cancelled) setAutomationsLoading(false); });
    return () => { cancelled = true; };
  }, [profile.id]);

  const toggleAutomation = async (automation) => {
    const nextStatus = automation.status === "active" ? "paused" : "active";
    setAutomations((rows) => rows.map((a) => a.id === automation.id ? { ...a, status: nextStatus } : a));
    try {
      const updated = await api.updateAutomation(automation.id, { status: nextStatus });
      setAutomations((rows) => rows.map((a) => a.id === automation.id ? updated : a));
    } catch (err) {
      setAutomations((rows) => rows.map((a) => a.id === automation.id ? { ...a, status: automation.status } : a));
      showToast("Couldn't update automation: " + (err.message || "unknown"), "high");
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="profile-detail-modal" onClick={(e) => e.stopPropagation()}>
        <button className="btn btn-sm modal-close-btn profile-modal-close-floating" onClick={onClose}>
          <X size={14} />
        </button>

        <div className="profile-modal-baseball">
          <CardArt cardId={profile.avatar} className="profile-modal-card-art" />
          {card && (
            <div className="profile-modal-archetype">
              <div className="profile-modal-archetype-name">{card.display_name}</div>
              <div className="profile-modal-archetype-tagline">{card.tagline}</div>
            </div>
          )}
        </div>

        <div className="modal-header profile-modal-identity-row">
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
              <Compass size={11} /> Automations
              {automations && automations.length > 0 && (
                <span className="profile-modal-goal-count">{automations.filter((a) => a.status === "active").length} active</span>
              )}
            </div>
            {automationsLoading ? (
              <div className="profile-modal-goals-empty">
                <Loader size={10} className="spin" /> Loading…
              </div>
            ) : !automations || automations.length === 0 ? (
              <div className="profile-modal-goals-empty">
                No automations watching for this agent yet. Build one on the Automations page.
              </div>
            ) : (
              <ul className="profile-modal-goal-list">
                {automations.map((a) => (
                  <li key={a.id} className={`profile-modal-goal status-${a.status}`}>
                    <div className="profile-modal-goal-main">
                      <span className="profile-modal-goal-kind">{a.name}</span>
                      {a.scope_repo && (
                        <span className="profile-modal-goal-repo" title={a.scope_repo}>
                          {formatRepo(a.scope_repo, defaultOrg)}
                        </span>
                      )}
                      <span className="profile-modal-goal-meta">
                        {describeAutomationTrigger(a)}
                        {a.last_fired_at && (
                          <> · fired {relativeTime(a.last_fired_at)}</>
                        )}
                      </span>
                    </div>
                    <button
                      className="btn btn-sm profile-modal-goal-toggle"
                      onClick={() => toggleAutomation(a)}
                      title={a.status === "active" ? "Pause this automation" : "Resume this automation"}
                      disabled={a.status === "archived"}
                    >
                      {a.status === "active" ? <Pause size={10} /> : <Play size={10} />}
                      {a.status === "active" ? " pause" : a.status === "paused" ? " resume" : " archived"}
                    </button>
                  </li>
                ))}
              </ul>
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