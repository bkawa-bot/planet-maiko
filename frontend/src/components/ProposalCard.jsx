import { useState } from "react";
import { Check, X, Pencil, Sparkles, Loader, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { formatRepo, useDefaultOrg } from "../utils/repo";

/**
 * Proposal card — a specialized pupdate renderer for type=agent_proposal
 * pupdates. Shows the proposing agent, the draft task, and approve /
 * edit / dismiss buttons. Approval creates a real routed task.
 *
 * Props:
 *   proposal  — pupdate dict where extra.draft holds the task draft.
 *   onAction  — () => void called after approve or dismiss so the parent
 *               list can refresh.
 */
export default function ProposalCard({ proposal, onAction }) {
  const defaultOrg = useDefaultOrg();
  const [editing, setEditing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const initialDraft = (proposal.metadata || proposal.extra || {}).draft || {};
  const [draft, setDraft] = useState({
    title: initialDraft.title || "",
    type: initialDraft.type || "todo",
    priority: initialDraft.priority || "normal",
    repo: initialDraft.repo || "",
    category: initialDraft.category || "",
    description: initialDraft.description || "",
  });
  const fromAgentId = (proposal.metadata || proposal.extra || {}).from_agent_id;

  const approve = async () => {
    setBusy(true);
    try {
      const payload = editing ? draft : initialDraft;
      const res = await api.approveProposal(proposal.id, payload);
      showToast(`Approved — task created${res.task?.assigned_agent_id ? ` (${res.task.assigned_agent_id})` : ""}`, "normal");
      onAction?.();
    } catch (err) {
      showToast(err.message || "Approve failed", "high");
    }
    setBusy(false);
  };

  const dismiss = async () => {
    setBusy(true);
    try {
      await api.dismissProposal(proposal.id);
      showToast("Proposal dismissed", "normal");
      onAction?.();
    } catch (err) {
      showToast(err.message || "Dismiss failed", "high");
    }
    setBusy(false);
  };

  return (
    <div className="proposal-card card">
      <div className="proposal-header">
        <Sparkles size={12} className="proposal-icon" />
        <span className="proposal-title">{proposal.title}</span>
        <button
          className="btn-ghost proposal-expand"
          onClick={() => setExpanded((v) => !v)}
          title={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
      </div>
      {fromAgentId && <div className="proposal-from">Proposed by {fromAgentId}</div>}
      {proposal.body && <div className="proposal-reasoning">{proposal.body}</div>}

      {expanded && (
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
        <button className="btn btn-sm" onClick={() => setEditing((v) => !v)} disabled={busy}>
          <Pencil size={10} /> {editing ? "Done editing" : "Edit"}
        </button>
        <button className="btn btn-sm btn-danger" onClick={dismiss} disabled={busy}>
          <X size={10} /> Dismiss
        </button>
        <button className="btn btn-sm btn-primary" onClick={approve} disabled={busy}>
          {busy ? <Loader size={10} className="spin" /> : <Check size={10} />} Approve
        </button>
      </div>
    </div>
  );
}
