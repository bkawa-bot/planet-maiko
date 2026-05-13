import { FolderOpen, X, ExternalLink } from "@icons";

/**
 * Read-only detail modal for a Task — shows description / url /
 * tags. Used as the legacy "open detail" path for tasks that don't
 * have their own dedicated page (e.g. todos).
 */
export default function TaskDetailModal({ task, onClose }) {
  if (!task) return null;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="info-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <FolderOpen size={14} /> {task.title}
          <span style={{ flex: 1 }} />
          <button
            className="btn btn-sm"
            onClick={onClose}
            style={{ border: "none", padding: 4 }}
          >
            <X size={14} />
          </button>
        </div>
        <div
          className="modal-body"
          style={{ fontSize: 13, lineHeight: 1.6, color: "var(--text-dim)" }}
        >
          {task.metadata?.description && (
            <div style={{ whiteSpace: "pre-wrap", marginBottom: 12 }}>
              {task.metadata.description}
            </div>
          )}
          {task.url && (
            <a
              href={task.url}
              target="_blank"
              rel="noreferrer"
              style={{ display: "block", marginBottom: 8 }}
            >
              <ExternalLink size={10} /> {task.url}
            </a>
          )}
          {task.tags?.length > 0 && (
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              {task.tags.map((tag) => (
                <span key={tag} className="tag">{tag}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
