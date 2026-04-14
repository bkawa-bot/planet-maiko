import { useState } from "react";
import { Check, Trash2, MessageSquare, Bot, User } from "lucide-react";
import { renderMarkdown } from "../../utils/markdown";

/**
 * Rendered via DiffView's `widgets` prop — sits under a diff line and
 * shows all comments anchored to that line + a reply box.
 *
 * Props:
 *   comments     — array of DiffComment dicts for this anchor, oldest first
 *   onReply      — (body, parentId?) => Promise<void>
 *   onEditDraft  — (id, body) => Promise<void>
 *   onDeleteDraft— (id) => Promise<void>
 *   onResolve    — (id) => Promise<void>
 *
 * Author styling: user comments use the pink accent, agent comments
 * use lavender + a Bot icon, so reviewers can eyeball the thread for
 * "is this something the agent flagged?"
 */
export default function CommentThread({ comments = [], onReply, onEditDraft, onDeleteDraft, onResolve }) {
  const [replyOpen, setReplyOpen] = useState(false);
  const [replyBody, setReplyBody] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const roots = comments.filter((c) => !c.parent_id);
  const repliesByParent = comments.reduce((acc, c) => {
    if (c.parent_id) {
      (acc[c.parent_id] = acc[c.parent_id] || []).push(c);
    }
    return acc;
  }, {});

  const submitReply = async () => {
    if (!replyBody.trim() || submitting) return;
    setSubmitting(true);
    try {
      // parent_id = the root of the thread if there is one
      const parentId = roots[0]?.id;
      await onReply?.(replyBody, parentId);
      setReplyBody("");
      setReplyOpen(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="diff-comment-thread">
      {roots.map((root) => (
        <CommentTree
          key={root.id}
          root={root}
          replies={repliesByParent[root.id] || []}
          onEditDraft={onEditDraft}
          onDeleteDraft={onDeleteDraft}
          onResolve={onResolve}
        />
      ))}
      {!replyOpen && roots.length > 0 && (
        <button className="diff-comment-reply-btn" onClick={() => setReplyOpen(true)}>
          <MessageSquare size={10} /> Reply
        </button>
      )}
      {replyOpen && (
        <div className="diff-comment-editor">
          <textarea
            value={replyBody}
            onChange={(e) => setReplyBody(e.target.value)}
            placeholder="Reply…"
            rows={3}
            autoFocus
          />
          <div className="diff-comment-editor-actions">
            <button className="btn btn-sm" onClick={() => { setReplyOpen(false); setReplyBody(""); }} disabled={submitting}>
              Cancel
            </button>
            <button className="btn btn-sm btn-primary" onClick={submitReply} disabled={submitting || !replyBody.trim()}>
              {submitting ? "…" : "Save draft"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function CommentTree({ root, replies, onEditDraft, onDeleteDraft, onResolve }) {
  return (
    <div className={`diff-comment-group status-${root.status}`}>
      <CommentBubble c={root} onEditDraft={onEditDraft} onDeleteDraft={onDeleteDraft} onResolve={onResolve} />
      {replies.map((r) => (
        <CommentBubble key={r.id} c={r} nested onEditDraft={onEditDraft} onDeleteDraft={onDeleteDraft} onResolve={onResolve} />
      ))}
    </div>
  );
}

function CommentBubble({ c, nested, onEditDraft, onDeleteDraft, onResolve }) {
  const [editing, setEditing] = useState(false);
  const [edit, setEdit] = useState(c.body);

  const isDraft = c.status === "draft";
  const isResolved = c.status === "resolved";
  const isAgent = c.author === "agent";
  const Icon = isAgent ? Bot : User;

  const saveEdit = async () => {
    await onEditDraft?.(c.id, edit);
    setEditing(false);
  };

  return (
    <div className={`diff-comment author-${c.author} ${nested ? "nested" : ""} ${isDraft ? "is-draft" : ""} ${isResolved ? "is-resolved" : ""}`}>
      <div className="diff-comment-header">
        <Icon size={10} />
        <span className="diff-comment-author">{c.author}</span>
        {isDraft && <span className="diff-comment-badge draft">draft</span>}
        {isResolved && <span className="diff-comment-badge resolved">resolved</span>}
        {c.status === "submitted" && <span className="diff-comment-badge submitted">submitted</span>}
      </div>
      {editing ? (
        <div className="diff-comment-editor">
          <textarea value={edit} onChange={(e) => setEdit(e.target.value)} rows={3} autoFocus />
          <div className="diff-comment-editor-actions">
            <button className="btn btn-sm" onClick={() => { setEditing(false); setEdit(c.body); }}>Cancel</button>
            <button className="btn btn-sm btn-primary" onClick={saveEdit}>Save</button>
          </div>
        </div>
      ) : (
        <div className="diff-comment-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(c.body || "") }} />
      )}
      <div className="diff-comment-actions">
        {isDraft && !editing && (
          <>
            <button className="diff-comment-action" onClick={() => setEditing(true)}>Edit</button>
            <button className="diff-comment-action danger" onClick={() => onDeleteDraft?.(c.id)}>
              <Trash2 size={9} /> Delete
            </button>
          </>
        )}
        {!isDraft && !isResolved && (
          <button className="diff-comment-action" onClick={() => onResolve?.(c.id)}>
            <Check size={9} /> Resolve
          </button>
        )}
      </div>
    </div>
  );
}
