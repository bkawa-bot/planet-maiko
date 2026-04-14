import { useEffect, useMemo, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Check, GitPullRequest, Loader, MessageSquare } from "lucide-react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import DiffView from "../components/diff/DiffView";
import CommentThread from "../components/diff/CommentThread";
import "./ReviewDiff.css";

/**
 * Full-page diff reviewer. Flow:
 *   1. Load diff + comments on mount.
 *   2. Render DiffView. Each line has a click-to-comment gutter.
 *   3. Comments with the same (file_path, line_number, side) cluster
 *      into one thread widget.
 *   4. Request changes → submits all drafts + wakes the agent.
 *   5. Approve → push + gh pr create + task done.
 */
export default function ReviewDiff() {
  const { taskId } = useParams();
  const navigate = useNavigate();

  const [diff, setDiff] = useState(null);
  const [comments, setComments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [approving, setApproving] = useState(false);
  const [newCommentAnchor, setNewCommentAnchor] = useState(null);  // { filePath, line, side, changeKey }
  const [newCommentBody, setNewCommentBody] = useState("");
  const [task, setTask] = useState(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [d, c, t] = await Promise.all([
        api.getTaskDiff(taskId).catch((e) => ({ error: e.message })),
        api.listDiffComments(taskId).catch(() => []),
        api.getTask(taskId).catch(() => null),
      ]);
      if (d?.error) {
        showToast(d.error, "high");
      } else {
        setDiff(d);
      }
      setComments(c || []);
      setTask(t);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Group comments by (file_path, line_number, side) so each thread
  // renders once even when there are multiple comments on the same
  // anchor. Keyed by the same changeKey DiffView uses internally —
  // computed lazily when we need the widgets map.
  const threadsByAnchor = useMemo(() => {
    const map = {};
    for (const c of comments) {
      const key = `${c.file_path}::${c.line_number}::${c.side}`;
      (map[key] = map[key] || []).push(c);
    }
    return map;
  }, [comments]);

  // Build the widgets map DiffView expects. We can't compute changeKey
  // from (file, line, side) alone — the library's getChangeKey uses
  // internal hunk indices. So DiffView handles matching via its
  // gutter handler, but for threads we parse the diff once to build
  // a (file, line, side) → changeKey index and reuse it here.
  // For MVP we take the simpler path: render threads as an "overview"
  // panel alongside the diff (not as inline widgets), so the user can
  // always see all comments even when we haven't wired widget anchors.
  // Inline widgets via the widgets prop come in a polish pass.
  const widgetsByKey = useMemo(() => ({}), []);

  const handleLineClick = (filePath, line, side) => {
    setNewCommentAnchor({ filePath, line, side });
    setNewCommentBody("");
  };

  const submitNewComment = async () => {
    if (!newCommentAnchor || !newCommentBody.trim()) return;
    try {
      await api.createDiffComment(taskId, {
        file_path: newCommentAnchor.filePath,
        line_number: newCommentAnchor.line,
        side: newCommentAnchor.side,
        body: newCommentBody,
        base_sha: diff?.base_sha,
      });
      setNewCommentAnchor(null);
      setNewCommentBody("");
      await fetchAll();
    } catch (e) {
      showToast(e.message, "high");
    }
  };

  const editDraft = async (id, body) => {
    await api.updateDiffComment(id, { body });
    await fetchAll();
  };

  const deleteDraft = async (id) => {
    await api.deleteDiffComment(id);
    await fetchAll();
  };

  const resolveComment = async (id) => {
    await api.updateDiffComment(id, { status: "resolved" });
    await fetchAll();
  };

  const replyInThread = async (parentAnchor, body, parentId) => {
    await api.createDiffComment(taskId, {
      file_path: parentAnchor.filePath,
      line_number: parentAnchor.line,
      side: parentAnchor.side,
      body,
      parent_id: parentId,
      base_sha: diff?.base_sha,
    });
    await fetchAll();
  };

  const drafts = comments.filter((c) => c.status === "draft" && c.author === "user");

  const handleRequestChanges = async () => {
    if (submitting || drafts.length === 0) return;
    setSubmitting(true);
    try {
      const result = await api.requestDiffChanges(taskId);
      showToast(
        `Sent ${result.submitted_comments} comment${result.submitted_comments === 1 ? "" : "s"} to the agent.`,
        "normal",
      );
      await fetchAll();
    } catch (e) {
      showToast(e.message, "high");
    } finally {
      setSubmitting(false);
    }
  };

  const handleApprove = async () => {
    if (approving) return;
    setApproving(true);
    try {
      const result = await api.approveDiffReview(taskId);
      if (result.pr_url) {
        showToast("Approved — PR opened", "normal");
      } else if (result.gh_installed === false) {
        showToast("Approved and branch pushed. Run `gh pr create` manually — gh CLI not found.", "high");
      } else {
        showToast("Approved and branch pushed.", "normal");
      }
      navigate("/tasks");
    } catch (e) {
      showToast(e.message, "high");
    } finally {
      setApproving(false);
    }
  };

  if (loading) {
    return (
      <div className="review-diff-page">
        <div className="review-diff-header">
          <Loader className="spin" size={14} /> Loading diff…
        </div>
      </div>
    );
  }

  const anchorKeys = Object.keys(threadsByAnchor);

  return (
    <div className="review-diff-page">
      <div className="review-diff-header">
        <button className="btn btn-sm" onClick={() => navigate(-1)}>
          <ArrowLeft size={10} /> Back
        </button>
        <div className="review-diff-title">
          {task?.title || `Task ${taskId}`}
          {diff?.base_branch && (
            <span className="review-diff-branch">
              {diff.base_branch} ← {diff.head_sha?.slice(0, 7)}
            </span>
          )}
        </div>
        <div className="review-diff-actions">
          <span className="review-diff-counter">
            <MessageSquare size={10} /> {drafts.length} draft{drafts.length === 1 ? "" : "s"}
          </span>
          <button
            className="btn btn-sm"
            onClick={handleRequestChanges}
            disabled={submitting || drafts.length === 0}
            title={drafts.length === 0 ? "Leave draft comments first" : "Send comments to the agent"}
          >
            {submitting ? <><Loader size={10} className="spin" /> Sending…</> : "Request changes"}
          </button>
          <button
            className="btn btn-sm btn-primary"
            onClick={handleApprove}
            disabled={approving}
          >
            {approving ? <><Loader size={10} className="spin" /> Opening PR…</> : <><GitPullRequest size={10} /> Approve &amp; open PR</>}
          </button>
        </div>
      </div>

      <div className="review-diff-layout">
        <div className="review-diff-main">
          <DiffView
            rawDiff={diff?.raw_diff}
            widgetsByKey={widgetsByKey}
            onLineClick={handleLineClick}
            viewType="unified"
          />
        </div>

        <aside className="review-diff-sidebar">
          <div className="review-diff-sidebar-title">
            <MessageSquare size={12} /> Comments
            {anchorKeys.length > 0 && <span className="review-diff-sidebar-count">{comments.length}</span>}
          </div>
          {anchorKeys.length === 0 && (
            <div className="review-diff-sidebar-empty">
              Click any line in the diff to leave a comment.
            </div>
          )}
          {anchorKeys.map((key) => {
            const threadComments = threadsByAnchor[key];
            const first = threadComments[0];
            const anchor = { filePath: first.file_path, line: first.line_number, side: first.side };
            return (
              <div key={key} className="review-diff-sidebar-thread">
                <div className="review-diff-sidebar-anchor">
                  <code>{first.file_path}:{first.line_number}</code>
                  {first.side === "old" && <span className="side-badge">old</span>}
                </div>
                <CommentThread
                  comments={threadComments}
                  onReply={(body, parentId) => replyInThread(anchor, body, parentId)}
                  onEditDraft={editDraft}
                  onDeleteDraft={deleteDraft}
                  onResolve={resolveComment}
                />
              </div>
            );
          })}
        </aside>
      </div>

      {newCommentAnchor && (
        <div className="review-diff-new-comment-overlay" onClick={() => setNewCommentAnchor(null)}>
          <div className="review-diff-new-comment" onClick={(e) => e.stopPropagation()}>
            <div className="review-diff-new-comment-header">
              <MessageSquare size={11} />
              <code>{newCommentAnchor.filePath}:{newCommentAnchor.line}</code>
              {newCommentAnchor.side === "old" && <span className="side-badge">old</span>}
            </div>
            <textarea
              value={newCommentBody}
              onChange={(e) => setNewCommentBody(e.target.value)}
              placeholder="Leave a comment…"
              rows={4}
              autoFocus
            />
            <div className="review-diff-new-comment-actions">
              <button className="btn btn-sm" onClick={() => setNewCommentAnchor(null)}>Cancel</button>
              <button
                className="btn btn-sm btn-primary"
                onClick={submitNewComment}
                disabled={!newCommentBody.trim()}
              >
                <Check size={10} /> Save draft
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
