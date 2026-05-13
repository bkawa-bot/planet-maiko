import { useState } from "react";
import { Check, X, Pencil, Sparkles, Loader, ChevronDown, ChevronRight, Compass } from "@icons";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import CardAvatar from "./CardAvatar";
// ProposalCard can be rendered anywhere (Home ReviewQueue, Tasks
// page, etc.) — pull in the card styles here so callers don't
// have to remember to import cards.css separately.
import "../pages/cards.css";

/**
 * Proposal card — renders agent_proposal Memos (new) and legacy
 * agent_proposal pupdates (until they age out).
 *
 * Two flavors based on what's in extra:
 *   - extra.draft set: TASK proposal — user approves to create a routed Task.
 *   - extra.proposed_goal set: GOAL proposal — approving installs an
 *     Automation so Maiko keeps watching the condition. No edit form;
 *     goals are tuned from the profile detail modal after adoption.
 *
 * Shape detection: Memos have a `kind` field and numeric `id`; pupdates
 * have a `type` field and string `id`. We branch the approve/dismiss
 * call sites off `isMemo` so the right endpoint runs.
 *
 * Props:
 *   proposal  — memo dict (new) or pupdate dict (legacy).
 *   profile   — optional AgentProfile dict for the proposing agent.
 *               When set + non-goal proposal, the header icon swaps to
 *               the agent's CardAvatar and the "Proposed by" line uses
 *               the resolved display_name.
 *   onAction  — () => void called after approve or dismiss so the parent
 *               list can refresh.
 */
export default function ProposalCard({ proposal, profile, onAction }) {
  const defaultOrg = useDefaultOrg();
  const [editing, setEditing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  // Memo has `kind`, pupdate has `type`. Neither has both, so this is
  // unambiguous even if someone adds more fields later.
  const isMemo = proposal?.kind != null && proposal?.type == null;
  const extra = proposal.metadata || proposal.extra || {};
  const proposedGoal = extra.proposed_goal;
  const isGoalProposal = !!proposedGoal;
  const initialDraft = extra.draft || {};
  const [draft, setDraft] = useState({
    title: initialDraft.title || "",
    type: initialDraft.type || "todo",
    priority: initialDraft.priority || "normal",
    repo: initialDraft.repo || "",
    category: initialDraft.category || "",
    description: initialDraft.description || "",
  });
  const fromAgentId = extra.from_agent_id;
  // Display name preference order: resolved profile > extra hint > raw id.
  const fromAgentName = profile?.display_name || extra.from_agent_display_name || fromAgentId;
  const showAvatar = !!profile && !isGoalProposal;

  const approve = async () => {
    setBusy(true);
    try {
      if (isGoalProposal) {
        // Goal proposals still flow through the pupdate path —
        // gap-detector hasn't migrated yet and these aren't memos.
        const res = await api.approveProposalAsGoal(proposal.id);
        const note = res.note === "already_installed" ? " (already tracked)" : "";
        showToast(`Goal adopted${note}`, "normal");
      } else if (isMemo) {
        // Memo approve lets us ship an edited draft by first PATCHing
        // extra.draft then calling /memos/<id>/approve. Keeps the
        // memo's on-the-fly editability without a parallel approve-
        // with-draft endpoint.
        if (editing) {
          await api.updateMemo(proposal.id, {
            extra: { ...extra, draft },
          });
        }
        const res = await api.approveMemo(proposal.id);
        const task = res?.result?.task;
        showToast(
          `Approved — task created${task?.assigned_agent_id ? ` (${task.assigned_agent_id})` : ""}`,
          "normal",
        );
      } else {
        const payload = editing ? draft : initialDraft;
        const res = await api.approveProposal(proposal.id, payload);
        showToast(`Approved — task created${res.task?.assigned_agent_id ? ` (${res.task.assigned_agent_id})` : ""}`, "normal");
      }
      onAction?.();
    } catch (err) {
      showToast(err.message || "Approve failed", "high");
    }
    setBusy(false);
  };

  const dismiss = async () => {
    setBusy(true);
    try {
      if (isMemo) {
        await api.dismissMemo(proposal.id);
      } else {
        await api.dismissProposal(proposal.id);
      }
      showToast("Proposal dismissed", "normal");
      onAction?.();
    } catch (err) {
      showToast(err.message || "Dismiss failed", "high");
    }
    setBusy(false);
  };

  return (
    <div className={`proposal-card card${isGoalProposal ? " proposal-card-goal" : ""}`}>
      <div className="proposal-header">
        {showAvatar ? (
          <span className="proposal-icon proposal-icon-avatar">
            <CardAvatar agent={profile} size={32} />
          </span>
        ) : isGoalProposal ? (
          <Compass size={12} className="proposal-icon" />
        ) : (
          <Sparkles size={12} className="proposal-icon" />
        )}
        <span className="proposal-title">{proposal.title}</span>
        {!isGoalProposal && (
          <button
            className="btn-ghost proposal-expand"
            onClick={() => setExpanded((v) => !v)}
            title={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </button>
        )}
      </div>
      {fromAgentId && <div className="proposal-from">Proposed by {fromAgentName}</div>}
      {isGoalProposal && (
        <div className="proposal-from">
          This becomes a standing goal — Maiko keeps watching the condition.
        </div>
      )}
      {proposal.body && <div className="proposal-reasoning">{proposal.body}</div>}

      {isGoalProposal && (
        <div className="proposal-goal-preview">
          <div className="proposal-goal-row">
            <span className="proposal-goal-label">Goal</span>
            <span className="proposal-goal-value">{formatGoalKind(proposedGoal.kind)}</span>
          </div>
          {proposedGoal.scope_repo && (
            <div className="proposal-goal-row">
              <span className="proposal-goal-label">Repo</span>
              <span className="proposal-goal-value" title={proposedGoal.scope_repo}>
                {formatRepo(proposedGoal.scope_repo, defaultOrg)}
              </span>
            </div>
          )}
          <div className="proposal-goal-row">
            <span className="proposal-goal-label">Role</span>
            <span className="proposal-goal-value">{proposedGoal.role}</span>
          </div>
          <div className="proposal-goal-row">
            <span className="proposal-goal-label">Trigger</span>
            <span className="proposal-goal-value">{describeTrigger(proposedGoal)}</span>
          </div>
        </div>
      )}

      {expanded && !isGoalProposal && (
        <div className="proposal-draft">
          <div className="proposal-draft-header">Draft task</div>
          {editing ? (
            <div className="proposal-draft-form">
              <input
                className="plan-task-title-input"
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                placeholder="Task title"
              />
              <div className="plan-task-row">
                <select className="plan-task-select" value={draft.priority}
                  onChange={(e) => setDraft({ ...draft, priority: e.target.value })}>
                  {["urgent", "high", "normal", "low"].map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
                <select className="plan-task-select" value={draft.type}
                  onChange={(e) => setDraft({ ...draft, type: e.target.value })}>
                  {["todo", "bug", "feature", "review", "investigation"].map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
                <input className="plan-task-input" placeholder="repo" value={draft.repo}
                  onChange={(e) => setDraft({ ...draft, repo: e.target.value })} />
                <input className="plan-task-input" placeholder="category" value={draft.category}
                  onChange={(e) => setDraft({ ...draft, category: e.target.value })} />
              </div>
              <textarea
                className="proposal-draft-desc"
                placeholder="Description"
                rows={3}
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              />
            </div>
          ) : (
            <div className="proposal-draft-view">
              <div className="proposal-draft-title">{draft.title}</div>
              <div className="proposal-draft-meta">
                <span className={`badge ${draft.priority}`}>{draft.priority}</span>
                <span className="tag">{draft.type}</span>
                {draft.repo && <span className="tag" title={draft.repo}>{formatRepo(draft.repo, defaultOrg)}</span>}
                {draft.category && <span className="tag">{draft.category}</span>}
              </div>
              {draft.description && <div className="proposal-draft-desc-view">{draft.description}</div>}
            </div>
          )}
        </div>
      )}

      <div className="proposal-actions">
        {!isGoalProposal && (
          <button
            className="btn btn-sm"
            onClick={() => {
              // Expand so the draft form is visible the moment you
              // click Edit — otherwise the form stays hidden behind
              // the collapsed state and nothing appears to happen.
              setEditing((v) => {
                const next = !v;
                if (next) setExpanded(true);
                return next;
              });
            }}
            disabled={busy}
          >
            <Pencil size={10} /> {editing ? "Done editing" : "Edit"}
          </button>
        )}
        <button className="btn btn-sm btn-danger" onClick={dismiss} disabled={busy}>
          <X size={10} /> Dismiss
        </button>
        <button className="btn btn-sm btn-primary" onClick={approve} disabled={busy}>
          {busy ? <Loader size={10} className="spin" /> : <Check size={10} />} {isGoalProposal ? "Adopt goal" : "Approve → create task"}
        </button>
      </div>
    </div>
  );
}


// Shared goal-kind label, duplicated intentionally from AgentsProfilesTab
// so ProposalCard doesn't force-import an agents-page component. Keep in
// sync if new kinds are added.
function formatGoalKind(kind) {
  const map = {
    keep_overview_current: "Keep overview current",
  };
  return map[kind] || kind.replace(/_/g, " ");
}

function describeTrigger(proposedGoal) {
  const cfg = proposedGoal.trigger_config || {};
  if (proposedGoal.kind === "keep_overview_current") {
    return `refresh after ${cfg.stale_days || 30}d stale`;
  }
  if (proposedGoal.trigger_kind === "cadence" && cfg.cadence_hours) {
    return `every ${cfg.cadence_hours}h`;
  }
  return proposedGoal.trigger_kind;
}
