import {
  CheckSquare, Square, FolderOpen, Pin, PinOff, ExternalLink,
  ChevronRight, GitBranch, Clock, Bot, Eye,
  X, Pencil, Brain, Circle,
} from "lucide-react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { formatDate } from "../utils/dates";

const STATUS_COLORS = {
  new: "var(--text-muted)", in_progress: "#60a5fa", waiting: "#fbbf24",
  review: "#a78bfa", done: "#4ade80", cancelled: "#6b7280",
};

const STATUS_ICONS = {
  new: Circle, in_progress: Square, waiting: Clock,
  review: Eye, done: CheckSquare, cancelled: X,
};

/**
 * A single task card. Renders both the collapsed and expanded states,
 * with all the inline editing controls (due date, project, agent assignment,
 * etc). Extracted from Tasks.jsx to keep that page focused on layout.
 *
 * Props:
 *   task                — Task object from /api/tasks
 *   isExpanded          — bool, controls expanded state (parent owns it)
 *   onToggleExpand      — () => void, called when card is clicked
 *   onAction            — (e, taskId, action) => void, for status transitions
 *   onAssignAgent       — (task) => void, opens the assign modal
 *   onEdit              — (task, editForm) => void, opens edit modal
 *   onAskMaiko          — (task) => void, opens Ask Maiko panel
 *   onShowDetail        — (task) => void, opens task detail modal
 *   onRefresh           — () => void, called after mutations
 *   projects            — Project[] for the project dropdown
 *   agentNames          — { [agentId]: displayName }
 */
export default function TaskCard({
  task: t,
  isExpanded,
  onToggleExpand,
  onAction,
  onAssignAgent,
  onEdit,
  onAskMaiko,
  onShowDetail,
  onRefresh,
  projects,
  agentNames,
}) {
  const statusColor = STATUS_COLORS[t.status] || "var(--text-muted)";
  const StatusIcon = STATUS_ICONS[t.status] || Circle;
  const priorityClass = t.priority || "normal";
  const isDone = t.status === "done" || t.status === "cancelled";
  const isPinned = !!(t.extra?.pinned || t.metadata?.pinned);

  const handleSourceIconClick = (e) => {
    e.stopPropagation();
    if (t.status === "new") onAction(e, t.id, "start");
    else if (t.status === "in_progress") onAction(e, t.id, "done");
  };

  const handleTogglePin = async () => {
    const extra = { ...(t.extra || t.metadata || {}), pinned: !isPinned };
    await api.updateTask(t.id, { extra });
    showToast(extra.pinned ? "Pinned to focus" : "Unpinned", "normal");
    onRefresh();
  };

  const handleDueDateChange = async (e) => {
    await api.updateTask(t.id, { due_date: e.target.value || null });
    showToast(e.target.value ? `Due: ${e.target.value}` : "Due date cleared", "normal");
    onRefresh();
  };

  const handleProjectChange = async (e) => {
    await api.updateTask(t.id, { project_id: e.target.value || null });
    showToast(e.target.value ? "Moved to project" : "Removed from project", "normal");
    onRefresh();
  };

  const handleEditClick = () => {
    onEdit(t, {
      title: t.title || "",
      description: t.extra?.description || t.metadata?.description || "",
      type: t.type || "todo",
      priority: t.priority || "normal",
      status: t.status || "new",
      project_id: t.project_id || "",
      url: t.url || "",
      due_date: t.due_date || "",
    });
  };

  return (
    <div
      className={`card pupdate-card ${priorityClass} ${isDone ? "read" : ""} ${isExpanded ? "expanded" : ""}`}
      onClick={onToggleExpand}
    >
      <div className="card-left-bar" style={{ background: statusColor }} />
      <div
        className="card-source-icon"
        onClick={handleSourceIconClick}
        style={{ cursor: t.status === "new" || t.status === "in_progress" ? "pointer" : "default" }}
        title={t.status === "new" ? "Click to start" : t.status === "in_progress" ? "Click to mark done" : t.status.replace("_", " ")}
      >
        <StatusIcon size={14} />
      </div>
      <div className="card-content">
        <div className="card-top">
          <span className="card-source" style={{ color: statusColor }}>{t.status.replace("_", " ")}</span>
          <span className="card-title">
            {t.url
              ? <a href={t.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{t.title}</a>
              : t.title}
          </span>
        </div>
        <div className="card-meta">
          <span className="card-type">{t.type}</span>
          {t.project_id && <span className="tag tag-project">{t.project_id}</span>}
          {(t.metadata?.repo || t.extra?.repo) && (
            <span className="tag"><GitBranch size={9} /> {t.metadata?.repo || t.extra?.repo}</span>
          )}
          {t.due_date && <span className="card-time"><Clock size={9} /> {t.due_date}</span>}
          {!t.due_date && t.updated_at && (
            <span className="card-time"><Clock size={9} /> {formatDate(t.updated_at)}</span>
          )}
          {t.assigned_agent_id && (
            <span className="tag agent-assigned-chip">
              <Bot size={9} /> {agentNames[t.assigned_agent_id] || t.assigned_agent_id.replace("agent-", "")}
            </span>
          )}
        </div>

        {isExpanded && (
          <>
            {t.tags?.length > 0 && (
              <div className="task-detail">
                {t.tags.map((tag) => <span key={tag} className="tag">{tag}</span>)}
              </div>
            )}
            <div className="task-inline-actions" onClick={(e) => e.stopPropagation()}>
              <input
                type="date"
                className="task-due-input"
                value={t.due_date || ""}
                onChange={handleDueDateChange}
                title="Set due date"
              />
              <select
                className="task-project-select"
                value={t.project_id || ""}
                onChange={handleProjectChange}
              >
                <option value="">No project</option>
                {(projects || []).map((p) => (
                  <option key={p.id} value={p.id}>{p.title}</option>
                ))}
              </select>
              {t.url && (
                <a href={t.url} target="_blank" rel="noreferrer" className="btn btn-sm" onClick={(e) => e.stopPropagation()}>
                  <ExternalLink size={10} /> Open
                </a>
              )}
              <button className="btn btn-sm btn-action" onClick={handleEditClick}>
                <Pencil size={10} /> Edit
              </button>
              {(t.type === "review" || t.type === "pr_review") && t.url && (
                <a href={t.url} target="_blank" rel="noreferrer" className="btn btn-sm btn-action" onClick={(e) => e.stopPropagation()}>
                  <Eye size={10} /> Review PR
                </a>
              )}
              {(t.extra?.description || t.metadata?.description || t.body) && (
                <button className="btn btn-sm btn-action" onClick={() => onShowDetail(t)}>
                  <FolderOpen size={10} /> Details
                </button>
              )}
              <button className="btn btn-sm btn-action" onClick={() => onAskMaiko(t)}>
                <Brain size={10} /> Ask Maiko
              </button>
              {!isDone && !t.assigned_agent_id && (
                <button className="btn btn-sm btn-action" onClick={() => onAssignAgent(t)}>
                  <Bot size={10} /> Assign Agent
                </button>
              )}
              {!isDone && (
                <button className="btn btn-sm btn-danger" onClick={(e) => onAction(e, t.id, "cancel")}>
                  <X size={10} /> Cancel
                </button>
              )}
            </div>
          </>
        )}
      </div>
      <div className="card-right" onClick={(e) => e.stopPropagation()}>
        <button
          className={`btn-ghost btn-pin ${isPinned ? "pinned" : ""}`}
          onClick={handleTogglePin}
          title={isPinned ? "Unpin from focus" : "Pin to focus"}
        >
          {isPinned ? <PinOff size={12} /> : <Pin size={12} />}
        </button>
        <span className={`card-priority badge ${priorityClass}`}>{t.priority}</span>
        <ChevronRight size={14} className={`card-chevron ${isExpanded ? "open" : ""}`} />
      </div>
    </div>
  );
}
