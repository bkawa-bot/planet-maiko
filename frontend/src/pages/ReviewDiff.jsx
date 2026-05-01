import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Check, ExternalLink, GitPullRequest, Loader, MessageSquare, PanelRightClose, PanelRightOpen, Sparkles, X } from "lucide-react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import DiffView from "../components/diff/DiffView";
import CommentThread from "../components/diff/CommentThread";
import { renderMarkdown } from "../utils/markdown";
import PlanetSpinner from "../components/PlanetSpinner";
import "./ReviewDiff.css";

const VERDICT_META = {
  approve:                { label: "Approve",              tone: "ok",    text: "agent approves" },
  approve_with_comments:  { label: "Approve w/ comments",  tone: "note",  text: "agent approves with comments" },
  soft_block:             { label: "Soft block",           tone: "warn",  text: "agent suggests fixing before merge" },
  hard_block:             { label: "Hard block",           tone: "stop",  text: "agent says do NOT merge as-is" },
};

/** Inline chip rendered on a diff line that has comments. Shows the
 *  count + a color reflecting who authored them. Clicking scrolls
 *  the sidebar thread into view and focus-rings it briefly. */
function InlineCommentMarker({ threadComments, onClick }) {
  const hasUser = threadComments.some((c) => c.author === "user");
  const hasAgent = threadComments.some((c) => c.author === "agent");
  const cls = hasUser && hasAgent ? "mixed" : hasAgent ? "has-agent" : "";
  return (
    <button
      className={`diff-inline-marker ${cls}`}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      title={`${threadComments.length} comment${threadComments.length === 1 ? "" : "s"} — click to view`}
    >
      <MessageSquare size={9} />
      <span className="diff-inline-marker-count">{threadComments.length}</span>
    </button>
  );
}

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
  const [focusedKey, setFocusedKey] = useState(null);
  // Toggle the right-rail comment sidebar off to read hunks full-width.
  // Persists for the session — the user who wants wide-mode for one
  // file usually wants it for the whole review.
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const threadRefs = useRef({});

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

  const scrollToThread = useCallback((key) => {
    const node = threadRefs.current[key];
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    setFocusedKey(key);
    // Let the focus ring fade after a beat so repeat clicks retrigger.
    setTimeout(() => setFocusedKey((prev) => (prev === key ? null : prev)), 1600);
  }, []);

  // Build the inline-marker map DiffView expects. Each anchor key
  // matches file_path::line::side (same shape DiffView uses internally)
  // and the value is a React node rendered on the matching diff line.
  const anchors = useMemo(() => {
    const map = {};
    for (const [key, threadComments] of Object.entries(threadsByAnchor)) {
      map[key] = (
        <InlineCommentMarker
          threadComments={threadComments}
          onClick={() => scrollToThread(key)}
        />
      );
    }
    return map;
  }, [threadsByAnchor, scrollToThread]);

  // Rules the agent retrieved via `maiko rules-relevant` while
  // working on this task — auto-recorded by the CLI. Dedupe across
  // queries (same rule may surface for several queries) and keep
  // each rule's best score so the highest-confidence match is what
  // the user sees. Also surface the queries themselves so the user
  // can see what the agent was thinking about, not just what came
  // back. Computed up here with the other useMemo calls so it
  // runs on every render — must NOT live below the loading early
  // return below or React trips Rules of Hooks.
  const { rulesConsidered, agentQueries } = useMemo(() => {
    const history = (task?.extra?.rules_considered || task?.metadata?.rules_considered || []);
    const byId = new Map();
    const querySet = new Set();
    for (const entry of history) {
      for (const r of (entry?.rules || [])) {
        if (r?.id == null) continue;
        const prior = byId.get(r.id);
        if (!prior || (r.score || 0) > (prior.score || 0)) byId.set(r.id, r);
      }
      for (const q of (entry?.queries || [])) {
        // Skip the placeholder the CLI inserts when no agent queries
        // were given (diff was decomposed by Haiku instead). Showing
        // it would just be noise.
        if (q && q !== "(diff-decomposed)") querySet.add(q);
      }
    }
    return {
      rulesConsidered: Array.from(byId.values()).sort((a, b) => (b.score || 0) - (a.score || 0)),
      agentQueries: Array.from(querySet),
    };
  }, [task]);

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
          <PlanetSpinner size={14} /> Loading…
        </div>
      </div>
    );
  }

  const anchorKeys = Object.keys(threadsByAnchor);

  // Review tasks vs coding tasks have different footer action shapes:
  // - Review task = agent reviewed someone else's PR. User can't
  //   "approve & open PR" from here — that's the GitHub workflow.
  //   We just let them close the review when they're done reading.
  // - Coding task = agent wrote code on their own branch. User
  //   approves/requests changes, and an Approve pushes + opens a PR.
  const isReviewTask = task && (task.type === "review" || task.type === "pr_review");
  const verdict = task?.metadata?.review_verdict || task?.extra?.review_verdict;
  const verdictMeta = verdict ? VERDICT_META[verdict] : null;
  const summary = task?.metadata?.review_summary || task?.extra?.review_summary;
  const prUrl = task?.url || task?.metadata?.pr_url || task?.extra?.pr_url;


  const handleCloseReview = async () => {
    if (!window.confirm("Close this review and clean up the worktree?")) return;
    try {
      await api.completeTask(taskId);
      showToast("Review closed.", "normal");
      navigate("/");
    } catch (e) {
      showToast(e.message || "Couldn't close the review", "high");
    }
  };

  return (
    <div className="review-diff-page">
      <div className="review-diff-header">
        <button className="btn btn-sm" onClick={() => navigate(-1)}>
          <ArrowLeft size={10} /> Back
        </button>
        <div className="review-diff-title">
          {task?.title || `Task ${taskId}`}
          {/* Only show the branch-chip for coding tasks — for review
              tasks the worktree is a scratch branch off main that
              doesn't represent meaningful state. */}
          {!isReviewTask && diff?.base_branch && (
            <span className="review-diff-branch">
              {diff.base_branch} ← {diff.head_sha?.slice(0, 7)}
            </span>
          )}
          {prUrl && (
            <a
              href={prUrl}
              target="_blank"
              rel="noreferrer"
              className="review-diff-pr-link"
              title="Open the PR on GitHub"
            >
              <ExternalLink size={10} /> open PR
            </a>
          )}
        </div>
        <div className="review-diff-actions">
          <button
            className="btn btn-sm"
            onClick={() => setSidebarHidden((v) => !v)}
            title={sidebarHidden ? "Show the comments sidebar" : "Hide the comments sidebar for wider diff"}
          >
            {sidebarHidden ? <PanelRightOpen size={10} /> : <PanelRightClose size={10} />}
            {sidebarHidden ? " show sidebar" : " wide"}
          </button>
          {isReviewTask ? (
            <button
              className="btn btn-sm"
              onClick={handleCloseReview}
              title="Mark this review finished and tear down the worktree"
            >
              <X size={10} /> Close review
            </button>
          ) : (
            <>
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
            </>
          )}
        </div>
      </div>

      {verdictMeta && (
        <div className={`review-verdict-banner verdict-${verdictMeta.tone}`}>
          <div className="review-verdict-chip">{verdictMeta.label}</div>
          <div className="review-verdict-body">
            <div className="review-verdict-label">{verdictMeta.text}</div>
            {summary && <div className="review-verdict-summary">{summary}</div>}
          </div>
        </div>
      )}

      {rulesConsidered.length > 0 && (
        <details className="rules-considered-panel" open={isReviewTask}>
          <summary className="rules-considered-summary">
            <Sparkles size={12} />
            <span>
              {rulesConsidered.length} team rule{rulesConsidered.length === 1 ? "" : "s"} considered
            </span>
            <span className="rules-considered-hint">
              what the agent had in mind during this work
            </span>
          </summary>
          {agentQueries.length > 0 && (
            <div className="rules-considered-queries">
              <div className="rules-considered-queries-label">
                Agent searched for
              </div>
              <ul className="rules-considered-queries-list">
                {agentQueries.map((q, i) => (
                  <li key={i} className="rules-considered-queries-item">
                    <span className="rules-considered-queries-quote">“</span>
                    {q}
                    <span className="rules-considered-queries-quote">”</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <ul className="rules-considered-list">
            {rulesConsidered.map((r) => (
              <li key={r.id} className="rules-considered-item">
                <span className="rules-considered-cat">[{r.category}]</span>
                <span className="rules-considered-rule">{r.rule}</span>
                <span
                  className="rules-considered-score"
                  title="Best cosine similarity across the agent's queries"
                >
                  {Math.round((r.score || 0) * 100)}%
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className={`review-diff-layout${sidebarHidden ? " sidebar-hidden" : ""}`}>
        <div className="review-diff-main">
          {/* Tasks that produced a markdown artifact (review reviews,
              investigation reports, repo_analysis findings) render the
              artifact as the primary content. The diff viewer is for
              coding tasks that produce actual code changes. Investigation
              and repo_analysis tasks technically aren't "review tasks"
              but they SHOULD land on TaskReport — this fallback catches
              cases where a stale memo route brings the user here for
              one of those, so the page shows the report instead of an
              empty diff. */}
          {task?.extra?.artifact ? (
            <div
              className="review-diff-artifact"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(task.extra.artifact) }}
            />
          ) : isReviewTask ? (
            <div className="review-diff-empty">
              {task && task.status === "review"
                ? "Review complete, but no artifact was saved. Check the agent's session log if you need the notes."
                : "Agent is still working on the review. Come back in a bit."}
            </div>
          ) : (
            <DiffView
              rawDiff={diff?.raw_diff}
              anchors={anchors}
              onLineClick={handleLineClick}
              viewType="unified"
            />
          )}
        </div>

        {!sidebarHidden && (
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
            const isFocused = focusedKey === key;
            return (
              <div
                key={key}
                ref={(el) => { threadRefs.current[key] = el; }}
                className={`review-diff-sidebar-thread ${isFocused ? "is-focused" : ""}`}
              >
                <div className="review-diff-sidebar-anchor">
                  {(() => {
                    const segs = (first.file_path || "").split("/");
                    const filename = segs.pop() || first.file_path;
                    const dir = segs.join("/");
                    return (
                      <>
                        {dir && <span className="sidebar-anchor-dir">{dir}/</span>}
                        <code className="sidebar-anchor-file">{filename}:{first.line_number}</code>
                      </>
                    );
                  })()}
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
        )}
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
