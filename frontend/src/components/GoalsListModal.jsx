import { useEffect, useState } from "react";
import { Compass, X, Loader, Pause, Play } from "lucide-react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import { relativeTime } from "../utils/dates";
import "./GoalsListModal.css";

/**
 * Global goals list shown when the user clicks the Home "Goals" chip.
 *
 * Closes the UX gap where the ambient count on Home had nowhere to
 * click through to — goals were only visible buried inside each
 * Profile Detail modal. This surface shows everything at once,
 * grouped by role, with enough detail to know what each goal watches
 * and pause controls when you want to silence one without archiving.
 *
 * Read-only on edit (no config-tuning UI here). To tune `stale_days`
 * or similar, still go into the profile modal — eventually that form
 * lives here too, but not today.
 */
export default function GoalsListModal({ onClose }) {
  const defaultOrg = useDefaultOrg();
  const [goals, setGoals] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchGoals = async () => {
    setLoading(true);
    try {
      const rows = await api.getGoals();
      setGoals(rows || []);
    } catch {
      setGoals([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchGoals(); }, []);

  const toggleGoal = async (goal) => {
    const nextStatus = goal.status === "active" ? "paused" : "active";
    setGoals((rows) => rows.map((g) => g.id === goal.id ? { ...g, status: nextStatus } : g));
    try {
      const updated = await api.updateGoal(goal.id, { status: nextStatus });
      setGoals((rows) => rows.map((g) => g.id === goal.id ? updated : g));
    } catch (err) {
      setGoals((rows) => rows.map((g) => g.id === goal.id ? { ...g, status: goal.status } : g));
      showToast("Couldn't update goal: " + (err.message || "unknown"), "high");
    }
  };

  // Group by role so the modal reads as "what each kind of agent is
  // watching for" rather than a flat list.
  const grouped = {};
  (goals || []).forEach((g) => {
    if (!grouped[g.role]) grouped[g.role] = [];
    grouped[g.role].push(g);
  });
  const roleOrder = Object.keys(grouped).sort();

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="goals-modal" onClick={(e) => e.stopPropagation()}>
        <div className="goals-modal-header">
          <Compass size={14} />
          <h3>Standing goals</h3>
          {goals && <span className="goals-modal-count">{goals.filter((g) => g.status === "active").length} active / {goals.length} total</span>}
          <button className="btn-ghost" onClick={onClose} title="Close"><X size={14} /></button>
        </div>

        <div className="goals-modal-body">
          {loading ? (
            <div className="goals-modal-empty"><Loader size={12} className="spin" /> Loading…</div>
          ) : !goals || goals.length === 0 ? (
            <div className="goals-modal-empty">
              No standing goals yet. Goals get seeded per configured repo on the next brain cycle, or adopted from gap proposals in the inbox.
            </div>
          ) : (
            <>
              <p className="goals-modal-blurb">
                Goals are durable watches each role-native agent holds. When a goal's condition fires, it posts a proposal to your inbox — nothing runs without your approval. Pause to silence without deleting.
              </p>
              {roleOrder.map((role) => (
                <section key={role} className="goals-modal-role">
                  <div className="goals-modal-role-label">{role}</div>
                  <ul className="goals-modal-list">
                    {grouped[role].map((g) => (
                      <li key={g.id} className={`goals-modal-row status-${g.status}`}>
                        <div className="goals-modal-row-main">
                          <span className="goals-modal-kind">{formatGoalKind(g.kind)}</span>
                          {g.scope_repo && (
                            <span className="goals-modal-repo" title={g.scope_repo}>
                              {formatRepo(g.scope_repo, defaultOrg)}
                            </span>
                          )}
                          <span className="goals-modal-meta">
                            {describeTrigger(g)}
                            {g.last_fired_at && <> · fired {relativeTime(g.last_fired_at)}</>}
                            {!g.last_fired_at && <> · never fired yet</>}
                          </span>
                        </div>
                        <button
                          className="btn btn-sm goals-modal-toggle"
                          onClick={() => toggleGoal(g)}
                          disabled={g.status === "archived"}
                          title={g.status === "active" ? "Pause this goal" : "Resume this goal"}
                        >
                          {g.status === "active" ? <Pause size={10} /> : <Play size={10} />}
                          {g.status === "active" ? " pause" : g.status === "paused" ? " resume" : " archived"}
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}


// Kept in sync with the same helpers in AgentsProfilesTab.jsx and
// ProposalCard.jsx. If we add a fourth consumer, extract them to
// utils/goals.js — for now the duplication is three short maps.
function formatGoalKind(kind) {
  const map = {
    keep_overview_current: "Keep overview current",
    train_lora_when_ready: "Train LoRA when rules accumulate",
  };
  return map[kind] || kind.replace(/_/g, " ");
}

function describeTrigger(goal) {
  const cfg = goal.trigger_config || {};
  if (goal.kind === "keep_overview_current") {
    return `refresh after ${cfg.stale_days || 30}d stale`;
  }
  if (goal.kind === "train_lora_when_ready") {
    return `watch for ${cfg.min_learnings || 10}+ rules, no adapter yet`;
  }
  if (goal.trigger_kind === "cadence" && cfg.cadence_hours) {
    return `every ${cfg.cadence_hours}h`;
  }
  return goal.trigger_kind;
}
