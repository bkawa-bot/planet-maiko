import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowLeft, Check, Loader, FileText, MessageSquare, GitBranch,
  Sparkles, Clock, AlertTriangle, Activity, GitPullRequest, X,
} from "lucide-react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { renderMarkdown } from "../utils/markdown";
import PlanetSpinner from "../components/PlanetSpinner";
import AgentChatThread from "../components/AgentChatThread";
import CardAvatar from "../components/CardAvatar";
import DiffView from "../components/diff/DiffView";
import CommentThread from "../components/diff/CommentThread";
import { relativeTime, formatTime } from "../utils/dates";
import "./AgentJobPage.css";

/**
 * Unified agent-job surface. Replaces the trio of ReviewDiff /
 * ReviewPlan / JobReport pages, plus pulls together the activity log
 * and chat thread that used to be sprinkled across other pages.
 *
 * Route: /jobs/:jobId
 * URL state: ?view=diff|plan|report|chat|activity (defaults to auto)
 *
 * Sections rendered conditionally on job.kind + data present:
 *   - Diff       coding / review / pr_review (worktree exists)
 *   - Plan       coding plan-first when a plan reply has landed
 *   - Report     investigation / repo_analysis / cartograph (artifact)
 *   - Chat       always (AgentChatThread on the job's inbox)
 *   - Activity   always (chronological pupdates + messages)
 *
 * Each section's logic lives inline here for V1. Old per-page routes
 * (ReviewDiff, ReviewPlan, JobReport) still resolve so existing
 * bookmarks don't break — App.jsx can redirect them in a follow-up.
 */
export default function AgentJobPage() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedView = searchParams.get("view");

  const [job, setJob] = useState(null);
  const [task, setTask] = useState(null);
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const j = await api.getAgentJob(jobId).catch(() => null);
      setJob(j);
      const tid = j?.source_task_id;
      const [t, profiles] = await Promise.all([
        tid ? api.getTask(tid).catch(() => null) : Promise.resolve(null),
        api.getProfiles().catch(() => []),
      ]);
      setTask(t);
      const p = (profiles || []).find((pp) => pp.id === j?.agent_profile_id);
      setProfile(p || null);
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const availableTabs = useMemo(() => computeTabs(job, task), [job, task]);
  const activeView = useMemo(
    () => resolveActiveView(requestedView, availableTabs, job, task),
    [requestedView, availableTabs, job, task],
  );

  const setView = (v) => {
    const next = new URLSearchParams(searchParams);
    next.set("view", v);
    setSearchParams(next);
  };

  if (loading) {
    return (
      <div className="agent-job-page">
        <div className="agent-job-loading"><PlanetSpinner size={14} /> Loading…</div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="agent-job-page">
        <div className="agent-job-header">
          <button className="btn btn-sm" onClick={() => navigate(-1)}>
            <ArrowLeft size={10} /> Back
          </button>
        </div>
        <div className="agent-job-empty">Job not found.</div>
      </div>
    );
  }

  return (
    <div className="agent-job-page">
      <JobHeader job={job} task={task} profile={profile} onBack={() => navigate(-1)} />
      <JobTabs tabs={availableTabs} active={activeView} onChange={setView} />
      <div className="agent-job-body">
        {activeView === "diff" && (
          <DiffPanel
            jobId={jobId}
            task={task}
            onChanged={fetchAll}
          />
        )}
        {activeView === "plan" && (
          <PlanPanel
            jobId={jobId}
            task={task}
            onChanged={fetchAll}
          />
        )}
        {activeView === "report" && (
          <ReportPanel job={job} task={task} />
        )}
        {activeView === "chat" && (
          <ChatPanel jobId={jobId} />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab resolution
// ---------------------------------------------------------------------------

function computeTabs(job, task) {
  if (!job) return [];
  const tabs = [];
  const isDiffKind = ["coding", "review", "pr_review"].includes(job.kind);
  const isReportKind = ["investigation", "repo_analysis", "cartograph"].includes(job.kind);
  const hasWorktree = !!job.worktree_path;
  const hasArtifact = !!(job.artifact || task?.metadata?.artifact);
  const taskExtra = task?.metadata || {};
  const hasPlan = !!taskExtra.plan;
  const hasPlanForApproval = !!taskExtra.plan && !taskExtra.plan_approved_at;

  if (isDiffKind && hasWorktree) {
    tabs.push({ id: "diff", label: "Diff", icon: GitBranch });
  }
  if (job.kind === "coding" && hasPlan) {
    tabs.push({
      id: "plan",
      label: hasPlanForApproval ? "Plan ●" : "Plan",
      icon: FileText,
    });
  }
  if (isReportKind || (hasArtifact && !isDiffKind)) {
    tabs.push({ id: "report", label: "Report", icon: FileText });
  }
  // Activity rolled into Chat — same data stream, the chat tab now
  // renders the activity-style cards and includes an input at the
  // bottom for sending replies. One tab, both behaviors.
  tabs.push({ id: "chat", label: "Chat", icon: MessageSquare });
  return tabs;
}

function resolveActiveView(requested, tabs, job, task) {
  if (requested && tabs.some((t) => t.id === requested)) return requested;
  // Default selection: highest-leverage first action.
  const taskExtra = task?.metadata || {};
  if (job?.kind === "coding" && taskExtra.plan && !taskExtra.plan_approved_at) {
    return "plan";
  }
  const isDiffKind = ["coding", "review", "pr_review"].includes(job?.kind);
  if (isDiffKind && job?.worktree_path) return "diff";
  const isReportKind = ["investigation", "repo_analysis", "cartograph"].includes(job?.kind);
  if (isReportKind || job?.artifact || taskExtra.artifact) return "report";
  return "chat";
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function JobHeader({ job, task, profile, onBack }) {
  const KIND_LABEL = {
    coding: "Coding", review: "Review", pr_review: "PR review",
    investigation: "Investigation", repo_analysis: "Repo analysis",
    cartograph: "Cartograph",
  };
  const STATUS_TONE = {
    queued: "muted", running: "active", done: "ready", failed: "warn",
    cancelled: "muted", pending_approval: "muted",
  };
  const tone = STATUS_TONE[job.status] || "muted";
  const title = task?.title || job.title || `Job ${job.id}`;
  return (
    <div className="agent-job-header">
      <button className="btn btn-sm" onClick={onBack}>
        <ArrowLeft size={10} /> Back
      </button>
      {profile && (
        <div className="agent-job-profile">
          <CardAvatar agent={profile} size={28} />
          <span className="agent-job-profile-name">{profile.display_name}</span>
        </div>
      )}
      <div className="agent-job-title">{title}</div>
      <span className={`agent-job-kind kind-${job.kind}`}>
        {KIND_LABEL[job.kind] || job.kind}
      </span>
      <span className={`agent-job-status status-${tone}`}>{job.status}</span>
      {job.scope_repo && (
        <span className="agent-job-repo">{job.scope_repo}</span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab bar
// ---------------------------------------------------------------------------

function JobTabs({ tabs, active, onChange }) {
  return (
    <div className="agent-job-tabs" role="tablist">
      {tabs.map((t) => {
        const Icon = t.icon;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active === t.id}
            className={`agent-job-tab ${active === t.id ? "is-active" : ""}`}
            onClick={() => onChange(t.id)}
          >
            <Icon size={11} /> {t.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Diff panel
// ---------------------------------------------------------------------------

function DiffPanel({ jobId, task, onChanged }) {
  // Every /tasks/<id>/... endpoint accepts either Task.id or
  // AgentJob.id post-canonicalization (_task_or_404 in diff_api.py
  // resolves either). So the panel passes jobId through the API
  // calls — no more awkward task?.id || jobId fallback shape, and
  // the page is consistently job-keyed end-to-end.
  const id = jobId;
  const [diff, setDiff] = useState(null);
  const [comments, setComments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [approving, setApproving] = useState(false);
  const [newAnchor, setNewAnchor] = useState(null);
  const [newBody, setNewBody] = useState("");
  // Bidirectional scroll state. Inline marker click → scrollToThread;
  // sidebar anchor click → scrollToDiffLine. Each direction sets a
  // brief focus flag so the user's eye lands on the destination.
  const threadRefs = useRef({});
  const inlineMarkerRefs = useRef({});
  const [focusedThreadKey, setFocusedThreadKey] = useState(null);
  const [focusedDiffKey, setFocusedDiffKey] = useState(null);
  const navigate = useNavigate();

  const refetch = useCallback(async () => {
    setLoading(true);
    try {
      const [d, c] = await Promise.all([
        api.getTaskDiff(id).catch((e) => ({ error: e.message })),
        api.listDiffComments(id).catch(() => []),
      ]);
      if (d?.error) showToast(d.error, "high");
      else setDiff(d);
      setComments(c || []);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { refetch(); }, [refetch]);

  const threadsByAnchor = useMemo(() => {
    const map = {};
    for (const c of comments) {
      const k = `${c.file_path}::${c.line_number}::${c.side}`;
      (map[k] = map[k] || []).push(c);
    }
    return map;
  }, [comments]);
  const anchorKeys = Object.keys(threadsByAnchor);

  const drafts = comments.filter((c) => c.status === "draft" && c.author === "user");

  const scrollToThread = useCallback((key) => {
    const node = threadRefs.current[key];
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    setFocusedThreadKey(key);
    setTimeout(() => setFocusedThreadKey((p) => (p === key ? null : p)), 1600);
  }, []);

  const scrollToDiffLine = useCallback((key) => {
    const node = inlineMarkerRefs.current[key];
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    setFocusedDiffKey(key);
    setTimeout(() => setFocusedDiffKey((p) => (p === key ? null : p)), 1600);
  }, []);

  const handleLineClick = (filePath, line, side) => {
    setNewAnchor({ filePath, line, side });
    setNewBody("");
  };
  const submitNew = async () => {
    if (!newAnchor || !newBody.trim()) return;
    try {
      await api.createDiffComment(id, {
        file_path: newAnchor.filePath,
        line_number: newAnchor.line,
        side: newAnchor.side,
        body: newBody,
        base_sha: diff?.base_sha,
      });
      setNewAnchor(null);
      setNewBody("");
      await refetch();
    } catch (e) { showToast(e.message, "high"); }
  };

  const handleRequestChanges = async () => {
    if (submitting || drafts.length === 0) return;
    setSubmitting(true);
    try {
      const r = await api.requestDiffChanges(id);
      showToast(`Sent ${r.submitted_comments} comment${r.submitted_comments === 1 ? "" : "s"} to the agent.`, "normal");
      await refetch();
      onChanged?.();
    } catch (e) { showToast(e.message, "high"); }
    setSubmitting(false);
  };

  const handleApprove = async () => {
    if (approving) return;
    setApproving(true);
    try {
      const r = await api.approveDiffReview(id);
      const url = r?.pr_url || r?.existing_pr_url;
      showToast(url ? `Approved — PR: ${url}` : "Approved", "normal");
      onChanged?.();
    } catch (e) { showToast(e.message, "high"); }
    setApproving(false);
  };

  // Rules the agent retrieved via `maiko rules-relevant` while doing
  // this task — auto-recorded by the CLI. Dedupe across queries (the
  // same rule may surface for several queries) and keep each rule's
  // best score so the highest-confidence match wins. Surface the
  // queries themselves too so the user can see what the agent had
  // in mind, not just what came back.
  //
  // Computed UP HERE (before the loading early return) so the hook
  // count is identical on every render — moving it below the
  // `if (loading)` branch tripped React's "rendered more hooks than
  // the previous render" rule on the second render after the diff
  // finished loading.
  const { rulesConsidered, agentQueries } = useMemo(() => {
    const history = task?.metadata?.rules_considered || [];
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
        // were given (diff was decomposed by Haiku instead).
        if (q && q !== "(diff-decomposed)") querySet.add(q);
      }
    }
    return {
      rulesConsidered: Array.from(byId.values()).sort((a, b) => (b.score || 0) - (a.score || 0)),
      agentQueries: Array.from(querySet),
    };
  }, [task]);

  if (loading) {
    return <div className="agent-job-loading"><PlanetSpinner size={12} /> Loading diff…</div>;
  }

  const verdict = task?.metadata?.review_verdict;
  const summary = task?.metadata?.review_summary;
  const artifact = task?.metadata?.artifact;

  return (
    <div className="agent-job-diff">
      {(verdict || summary) && (
        <div className={`diff-verdict-banner verdict-${verdict || "neutral"}`}>
          {verdict && <span className="verdict-chip">{verdict.replace(/_/g, " ")}</span>}
          {summary && <span className="verdict-summary">{summary}</span>}
        </div>
      )}
      {artifact && (
        <details className="diff-agent-notes">
          <summary>Agent's notes</summary>
          <div
            className="diff-agent-notes-body"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(artifact) }}
          />
        </details>
      )}
      {rulesConsidered.length > 0 && (
        <details className="rules-considered-panel">
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
              <div className="rules-considered-queries-label">Agent searched for</div>
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
      <div className="agent-job-diff-grid">
        <div className="agent-job-diff-main">
          {diff?.raw_diff ? (
            <DiffView
              rawDiff={diff.raw_diff}
              anchors={Object.fromEntries(
                Object.entries(threadsByAnchor).map(([k, cs]) => [
                  k,
                  <InlineMarker
                    key={k}
                    count={cs.length}
                    focused={focusedDiffKey === k}
                    onClick={() => scrollToThread(k)}
                    registerRef={(el) => {
                      if (el) inlineMarkerRefs.current[k] = el;
                      else delete inlineMarkerRefs.current[k];
                    }}
                  />,
                ])
              )}
              onLineClick={handleLineClick}
              viewType="unified"
            />
          ) : (
            <div className="agent-job-empty">No diff yet — agent may still be working.</div>
          )}
          {diff?.untracked_files?.length > 0 && (
            <div className="diff-untracked-hint">
              <AlertTriangle size={11} /> Untracked: {diff.untracked_files.slice(0, 5).join(", ")}
              {diff.untracked_files.length > 5 && ` (+${diff.untracked_files.length - 5} more)`}
            </div>
          )}
        </div>
        <aside className="agent-job-diff-sidebar">
          <div className="diff-sidebar-title">
            <MessageSquare size={11} /> Comments
            <span className="diff-sidebar-count">{comments.length}</span>
          </div>
          {anchorKeys.length === 0 && (
            <div className="diff-sidebar-empty">Click any line to leave a comment.</div>
          )}
          {anchorKeys.map((key) => {
            const cs = threadsByAnchor[key];
            const first = cs[0];
            const anchor = { filePath: first.file_path, line: first.line_number, side: first.side };
            const isFocused = focusedThreadKey === key;
            return (
              <div
                key={key}
                ref={(el) => { threadRefs.current[key] = el; }}
                className={`diff-sidebar-thread ${isFocused ? "is-focused" : ""}`}
              >
                <button
                  type="button"
                  className="diff-sidebar-anchor"
                  onClick={() => scrollToDiffLine(key)}
                  title="Jump to this line in the diff"
                >
                  <code>{first.file_path}:{first.line_number}</code>
                </button>
                <CommentThread
                  comments={cs}
                  onReply={async (body, parentId) => {
                    await api.createDiffComment(id, {
                      file_path: anchor.filePath,
                      line_number: anchor.line,
                      side: anchor.side,
                      body, parent_id: parentId, base_sha: diff?.base_sha,
                    });
                    refetch();
                  }}
                  onEditDraft={async (id, body) => { await api.updateDiffComment(id, { body }); refetch(); }}
                  onDeleteDraft={async (id) => { await api.deleteDiffComment(id); refetch(); }}
                  onResolve={async (id) => { await api.updateDiffComment(id, { status: "resolved" }); refetch(); }}
                />
              </div>
            );
          })}
        </aside>
      </div>
      {diff?.raw_diff && (
        <div className="agent-job-diff-actions">
          <span className="diff-actions-hint">
            {drafts.length === 0
              ? "Click any line in the diff to leave a comment, then send."
              : `${drafts.length} draft${drafts.length === 1 ? "" : "s"} ready to send.`}
          </span>
          <button
            className="btn"
            disabled={submitting || drafts.length === 0}
            onClick={handleRequestChanges}
            title={drafts.length === 0 ? "Leave at least one draft comment first" : "Send all drafts to the agent"}
          >
            {submitting ? <Loader size={11} className="spin" /> : <MessageSquare size={11} />}
            {" "}Request changes{drafts.length > 0 ? ` (${drafts.length})` : ""}
          </button>
          <button
            className="btn btn-primary"
            disabled={approving || drafts.length > 0}
            onClick={handleApprove}
            title={drafts.length > 0 ? "Resolve or send pending drafts first" : ""}
          >
            {approving ? <Loader size={11} className="spin" /> : <Check size={11} />}
            {" "}Approve & open PR
          </button>
        </div>
      )}
      {newAnchor && (
        <div className="agent-job-new-comment-overlay" onClick={() => setNewAnchor(null)}>
          <div className="agent-job-new-comment" onClick={(e) => e.stopPropagation()}>
            <div className="new-comment-header">
              <code>{newAnchor.filePath}:{newAnchor.line}</code>
            </div>
            <textarea
              value={newBody}
              onChange={(e) => setNewBody(e.target.value)}
              placeholder="Leave a comment…"
              rows={4}
              autoFocus
            />
            <div className="new-comment-actions">
              <button className="btn btn-sm" onClick={() => setNewAnchor(null)}>Cancel</button>
              <button
                className="btn btn-sm btn-primary"
                onClick={submitNew}
                disabled={!newBody.trim()}
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

function InlineMarker({ count, focused, onClick, registerRef }) {
  return (
    <button
      ref={registerRef}
      className={`diff-inline-marker ${focused ? "is-focused" : ""}`}
      onClick={(e) => { e.stopPropagation(); onClick?.(); }}
      title={`${count} comment${count === 1 ? "" : "s"} — click to view`}
    >
      <MessageSquare size={9} />
      <span>{count}</span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Plan panel
// ---------------------------------------------------------------------------

function PlanPanel({ jobId, task, onChanged }) {
  // Same canonicalization story as DiffPanel — the backend's
  // /tasks/<id>/plan endpoints accept either Task.id or Job.id, so
  // the panel just passes jobId through.
  const id = jobId;
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [revising, setRevising] = useState(false);
  const [approving, setApproving] = useState(false);
  const [feedback, setFeedback] = useState("");

  const refetch = useCallback(async () => {
    setLoading(true);
    try {
      const p = await api.getTaskPlan(id).catch(() => null);
      setPlan(p);
    } finally { setLoading(false); }
  }, [id]);
  useEffect(() => { refetch(); }, [refetch]);

  const handleApprove = async () => {
    if (approving) return;
    setApproving(true);
    try {
      await api.approveTaskPlan(id);
      showToast("Plan approved — agent is implementing.", "normal");
      onChanged?.();
      refetch();
    } catch (e) { showToast(e.message, "high"); }
    setApproving(false);
  };

  const handleRevise = async () => {
    if (!feedback.trim() || revising) return;
    setRevising(true);
    try {
      await api.reviseTaskPlan(id, feedback);
      showToast("Revision requested.", "normal");
      setFeedback("");
      onChanged?.();
      refetch();
    } catch (e) { showToast(e.message, "high"); }
    setRevising(false);
  };

  if (loading) {
    return <div className="agent-job-loading"><PlanetSpinner size={12} /> Loading plan…</div>;
  }

  const hasPlan = plan?.plan && plan.plan.trim();
  const approved = !!plan?.plan_approved_at;

  if (!hasPlan) {
    return <div className="agent-job-empty">No plan yet — the agent is still working on it.</div>;
  }

  return (
    <div className="agent-job-plan">
      {approved && (
        <div className="plan-banner approved">
          <Check size={11} /> Plan approved {plan.plan_approved_at ? `· ${relativeTime(plan.plan_approved_at)}` : ""}
        </div>
      )}
      <div
        className="plan-body markdown"
        dangerouslySetInnerHTML={{ __html: renderMarkdown(plan.plan) }}
      />
      {!approved && (
        <div className="plan-actions">
          <div className="plan-revise">
            <div className="plan-revise-label">
              <MessageSquare size={11} /> Request changes
            </div>
            <textarea
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              placeholder="What would you change about the plan?"
              rows={4}
            />
            <button
              className="btn"
              onClick={handleRevise}
              disabled={revising || !feedback.trim()}
            >
              {revising ? <><Loader size={10} className="spin" /> Sending…</> : "Request revision"}
            </button>
          </div>
          <button
            className="btn btn-primary plan-approve"
            onClick={handleApprove}
            disabled={approving}
          >
            {approving ? <><Loader size={10} className="spin" /> Starting…</> : <><Check size={12} /> Approve & implement</>}
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Report panel
// ---------------------------------------------------------------------------

function ReportPanel({ job, task }) {
  const artifact = job?.artifact || task?.metadata?.artifact;
  if (!artifact || !artifact.trim()) {
    return (
      <div className="agent-job-empty">
        {job?.status === "running"
          ? "Agent is still working — report will appear here when they finish."
          : job?.status === "failed"
            ? `Job failed${job?.error ? `: ${job.error}` : "."}`
            : "No report on this job yet."}
      </div>
    );
  }
  return (
    <div className="agent-job-report markdown">
      <div dangerouslySetInnerHTML={{ __html: renderMarkdown(artifact) }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat panel — combined activity feed + reply input
//
// Activity (read-only chronology) and Chat (input + transcript) used to
// be separate tabs but rendered the same AgentMessage stream. Folded
// into one: the activity-style cards above (same color-coded
// border-left for stuck / ready_for_review / plan_for_approval) plus
// a textarea + Send below for new replies.
// ---------------------------------------------------------------------------

function ChatPanel({ jobId }) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const endRef = useRef(null);

  const refetch = useCallback(async () => {
    try {
      const m = await api.getAgentMessages(jobId);
      setMessages(Array.isArray(m) ? m : []);
    } catch {
      // Best-effort — keep prior list on transient failures.
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  // Initial load + 8s poll while the panel's open. Same cadence
  // AgentChatThread uses for its own polling.
  useEffect(() => {
    refetch();
    const t = setInterval(refetch, 8000);
    return () => clearInterval(t);
  }, [refetch]);

  // Snap to the bottom whenever the message count changes — opening
  // the panel or sending a new message lands on the latest entry.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const res = await api.sendToAgent(jobId, { content: text, sender: "user" });
      const mode = res?.wake_mode;
      if (mode === "woke") showToast("Message sent — waking the agent ✨", "normal");
      else if (mode === "queued") showToast("Agent's working — queued for the next turn", "normal");
      else if (mode === "error") showToast("Sent, but agent has no live session to wake", "high");
      else showToast("Message saved to inbox", "normal");
      setInput("");
      await refetch();
    } catch (err) {
      showToast(err.message || "Couldn't send", "high");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="agent-job-chat">
      <div className="chat-thread">
        {loading ? (
          <div className="agent-job-loading"><PlanetSpinner size={12} /> Loading…</div>
        ) : messages.length === 0 ? (
          <div className="agent-job-empty">No messages yet. Send something below — they'll respond on their next check-in.</div>
        ) : (
          <ul className="activity-list">
            {messages.map((m) => (
              <li
                key={m.id}
                className={`activity-item dir-${m.direction} type-${m.message_type || "message"}`}
              >
                <div className="activity-meta">
                  <span className="activity-sender">{m.sender}</span>
                  {m.message_type && m.message_type !== "message" && (
                    <span className="activity-type">{m.message_type}</span>
                  )}
                  <span className="activity-time" title={formatTime(m.created_at)}>
                    {relativeTime(m.created_at)}
                  </span>
                </div>
                <div className="activity-content">{m.content}</div>
              </li>
            ))}
          </ul>
        )}
        <div ref={endRef} />
      </div>
      <div className="chat-input">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Cmd/Ctrl+Enter sends; plain Enter inserts a newline so
            // multi-line messages are easy.
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="Ask a follow-up… ⌘+Enter to send"
          rows={3}
          disabled={sending}
        />
        <button
          className="btn btn-primary"
          onClick={handleSend}
          disabled={sending || !input.trim()}
        >
          {sending ? <Loader size={11} className="spin" /> : <MessageSquare size={11} />}
          {" "}Send
        </button>
      </div>
    </div>
  );
}
