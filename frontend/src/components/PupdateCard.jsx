import {
  CheckSquare, FolderKanban, ChevronRight, Eye, Search, X,
} from "lucide-react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { formatDateTime } from "../utils/dates";

/**
 * A single pupdate card. Used by Inbox.jsx for both the "from_maiko" and
 * default tabs (previously duplicated ~180 lines twice).
 *
 * Props:
 *   pupdate          — pupdate object from /api/pupdates
 *   isExpanded       — bool
 *   onToggleExpand   — () => void
 *   onMarkRead       — (id) => void
 *   onDismiss        — (e, id) => void
 *   onReviewPR       — (pupdate) => void
 *   reviewing        — id of pupdate currently being reviewed (for spinner state)
 *   sourceIcon       — (source) => React node, for the source-type icon
 *   showQuickDismiss — bool, whether the top-right has a quick-dismiss button
 */
export default function PupdateCard({
  pupdate: p,
  isExpanded,
  onToggleExpand,
  onMarkRead,
  onDismiss,
  onReviewPR,
  reviewing,
  sourceIcon,
  showQuickDismiss = false,
}) {
  const handleCreateTask = async () => {
    try {
      await api.createTask({
        id: `task-${p.id}`,
        title: p.title,
        type: "todo",
        priority: p.priority,
        source_pupdate_id: p.id,
        url: p.url || "",
        tags: p.tags || [],
      });
      showToast(`Task created: ${p.title.slice(0, 40)}...`, "normal");
      onMarkRead(p.id);
    } catch (err) {
      showToast("Couldn't create task", "high");
    }
  };

  const handleCreateProject = async () => {
    try {
      await api.createProject({
        id: `proj-${p.id}`,
        title: p.title,
        description: p.body || "",
        priority: p.priority || "normal",
      });
      showToast(`Project created: ${p.title.slice(0, 40)}...`, "normal");
      onMarkRead(p.id);
    } catch (err) {
      showToast("Couldn't create project", "high");
    }
  };

  return (
    <div
      className={`card pupdate-card ${p.priority} ${p.read ? "read" : ""} ${isExpanded ? "expanded" : ""}`}
      onClick={onToggleExpand}
    >
      <div className="card-left-bar" />
      <div className="card-source-icon">{sourceIcon(p.source)}</div>
      <div className="card-content">
        <div className="card-top">
          <span className="card-source">{p.source}</span>
          <span className="card-title">
            {p.url
              ? <a href={p.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{p.title}</a>
              : p.title}
          </span>
        </div>
        <div className="card-meta">
          <span className="card-type">{p.type?.replace(/_/g, " ")}</span>
          <span className="card-time">{formatDateTime(p.timestamp)}</span>
          {p.actionable && (
            <button
              className="card-action-hint"
              onClick={(e) => { e.stopPropagation(); onToggleExpand(); }}
            >
              {p.action_hint}
            </button>
          )}
        </div>

        {isExpanded && p.body && <div className="rich-body">{p.body}</div>}

        {isExpanded && (
          <div className="card-inline-actions" onClick={(e) => e.stopPropagation()}>
            {p.type === "pr_review_requested" && (
              <button
                className="btn btn-sm btn-action"
                onClick={() => onReviewPR(p)}
                disabled={reviewing === p.id}
              >
                <Eye size={10} /> {reviewing === p.id ? "Reviewing..." : "Review PR"}
              </button>
            )}
            {(p.type === "pr_ci_failed" || p.type === "incident") && (
              <button className="btn btn-sm btn-session"><Search size={10} /> Investigate</button>
            )}
            {p.type !== "suggestion" && (
              <button className="btn btn-sm btn-create" onClick={handleCreateTask}>
                <CheckSquare size={10} /> Create Task
              </button>
            )}
            <button className="btn btn-sm btn-approve" onClick={handleCreateProject}>
              <FolderKanban size={10} /> Create Project
            </button>
            <button className="btn btn-sm btn-danger" onClick={(e) => onDismiss(e, p.id)}>
              <X size={10} /> Dismiss
            </button>
            {p.tags?.length > 0 && (
              <div className="card-tags-inline">
                {p.tags.map((t) => <span key={t} className="tag">{t}</span>)}
              </div>
            )}
          </div>
        )}
      </div>
      <div className="card-right">
        {showQuickDismiss && (
          <button
            className="btn-ghost btn-dismiss-quick"
            onClick={(e) => onDismiss(e, p.id)}
            title="Dismiss"
          >
            <X size={12} />
          </button>
        )}
        <span className={`card-priority badge ${p.priority}`}>{p.priority}</span>
        <ChevronRight size={14} className={`card-chevron ${isExpanded ? "open" : ""}`} />
      </div>
    </div>
  );
}
