import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
import {
  Check, Loader, FileText, MessageSquare,
  Sparkles, Clock, AlertTriangle, Activity, GitPullRequest, X,
  PanelRightClose, PanelRightOpen, CheckboxTree, ChatBubble, ExternalLink,
} from "@icons";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { renderMarkdown } from "../utils/markdown";
import PlanetSpinner from "../components/PlanetSpinner";
import CardAvatar from "../components/CardAvatar";
import ProposalCard from "../components/ProposalCard";
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
 *   - Chat       always (inline ChatPanel on the job's inbox)
 *   - Activity   always (chronological pupdates + messages)
 *
 * Each section's logic lives inline here for V1. Old per-page routes
 * (ReviewDiff, ReviewPlan, JobReport) still resolve so existing
 * bookmarks don't break — App.jsx can redirect them in a follow-up.
 */
export default function AgentJobPage() {
  const { jobId } = useParams();
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
      <div className="agent-job-page frost-pane">
        <div className="agent-job-loading"><PlanetSpinner size={14} /> Loading…</div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="agent-job-page frost-pane">
        <div className="agent-job-empty">Job not found.</div>
      </div>
    );
  }

  return (
    <div className="agent-job-page frost-pane">
      <JobHeader job={job} task={task} profile={profile} />
      <JobTabs tabs={availableTabs} active={activeView} onChange={setView} />
      <div className="agent-job-body">
        {activeView === "diff" && (
          <DiffPanel
            jobId={jobId}
            job={job}
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
          <ChatPanel jobId={jobId} agentName={profile?.display_name} />
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
  const isReportKind = ["investigation", "repo_analysis", "cartograph"].includes(job.kind);
  const hasWorktree = !!job.worktree_path;
  // Any non-report job with a worktree produces a diff — including
  // todo/bug/feature tasks an agent coded on (job.kind mirrors
  // task.type, so it's literally "todo" there, not "coding"). Gating
  // the Diff tab on kind=="coding" hid those diffs entirely; gate on
  // "has a worktree and isn't a written-report kind" instead.
  const isDiffKind = hasWorktree && !isReportKind;
  const hasArtifact = !!(job.artifact || task?.metadata?.artifact);
  const taskExtra = task?.metadata || {};
  // Plan content actually lives in an AgentMessage row (the agent's
  // `reply --type plan_for_approval`), fetched by /jobs/<id>/plan
  // inside PlanPanel. We don't have that here, so use the durable
  // signal that IS on the task: plan_first (set at assign/spawn
  // time). taskExtra.plan stays in the OR for any legacy code path
  // that DID mirror the content onto the task.
  const hasPlan = !!taskExtra.plan_first || !!taskExtra.plan;
  const hasPlanForApproval = hasPlan && !taskExtra.plan_approved_at;

  if (isDiffKind) {
    tabs.push({ id: "diff", label: "Diff", icon: CheckboxTree });
  }
  if (hasPlan) {
    // Plan tab is no longer gated on kind=="coding". A job that went
    // investigation -> coding via `maiko handoff` keeps the plan; a
    // coding job that did plan-first surfaces it the same way.
    tabs.push({
      id: "plan",
      label: hasPlanForApproval ? "Plan ●" : "Plan",
      icon: FileText,
    });
  }
  if (isReportKind || hasArtifact) {
    // Report tab now shows whenever there's a written artifact, even
    // if the current kind is a diff-producing one. Lets a coding agent
    // that started as an investigation keep its prior report visible
    // alongside the new diff.
    tabs.push({ id: "report", label: "Report", icon: FileText });
  }
  // Activity rolled into Chat — same data stream, the chat tab now
  // renders the activity-style cards and includes an input at the
  // bottom for sending replies. One tab, both behaviors.
  tabs.push({ id: "chat", label: "Chat", icon: ChatBubble });
  return tabs;
}

function resolveActiveView(requested, tabs, job, task) {
  if (requested && tabs.some((t) => t.id === requested)) return requested;
  // Default selection: highest-leverage first action.
  const taskExtra = task?.metadata || {};
  // Same plan_first / plan signal as computeTabs — the actual plan
  // content lives in AgentMessage rows, not on the task.
  const hasPlan = !!taskExtra.plan_first || !!taskExtra.plan;
  if (job?.kind === "coding" && hasPlan && !taskExtra.plan_approved_at) {
    return "plan";
  }
  const isReportKind = ["investigation", "repo_analysis", "cartograph"].includes(job?.kind);
  // Same rule as availableTabs: a worktree on a non-report job means
  // there's a diff to land on, whatever the literal kind string is.
  if (job?.worktree_path && !isReportKind) return "diff";
  if (isReportKind || job?.artifact || taskExtra.artifact) return "report";
  return "chat";
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function JobHeader({ job, task, profile }) {
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
  const kindLabel = KIND_LABEL[job.kind] || job.kind;
  const [resuming, setResuming] = useState(false);
  // Terminal pop-out (moved here from the active-agents card). The
  // resume-session endpoint accepts job_id or task_id; jobId is the
  // AgentJob id. Show it once the job has actually started — a queued
  // job has no session/worktree to attach to yet.
  const canResume = job.status === "running" || !!job.worktree_path;
  const openSession = async () => {
    if (resuming) return;
    setResuming(true);
    try {
      const r = await api.resumeAgentSession(job.id);
      showToast(
        r?.mode === "tmux" ? "Attaching to the agent's tmux…"
          : r?.mode === "resume" ? "Resuming the agent's session…"
          : "Opening a live view…",
        "normal",
      );
    } catch (err) {
      showToast(err.message || "No live session to open", "high");
    } finally {
      setResuming(false);
    }
  };
  return (
    <div className="agent-job-header">
      <div className="agent-job-header-top">
        <span className={`agent-job-kind kind-${job.kind}`}>{kindLabel}</span>
        <span className={`agent-job-status status-${tone}`}>{job.status}</span>
        {job.scope_repo && (
          <span className="agent-job-repo">{job.scope_repo}</span>
        )}
        {(() => {
          // pr_url lives on the task's `extra` JSON (serialized to the
          // client as `metadata`) and is mirrored onto task.url; a
          // job-level PR with no linked task keeps it on job.extra.
          // Check all three so the button surfaces whichever path
          // actually opened the PR.
          const prUrl = job?.extra?.pr_url
            || task?.metadata?.pr_url
            || (task?.url && /github\.com\/[^/]+\/[^/]+\/pull\//.test(task.url) ? task.url : null);
          return prUrl ? (
            <a
              className="btn btn-sm"
              href={prUrl}
              target="_blank"
              rel="noreferrer"
              title="Open the linked pull request on GitHub"
            >
              <GitPullRequest size={10} /> View PR
            </a>
          ) : null;
        })()}
        {canResume && (
          <button className="btn btn-sm" onClick={openSession} disabled={resuming}
            title="Attach to the agent's session in a terminal">
            {resuming
              ? <><Loader size={10} className="spin" /> Opening…</>
              : <><ExternalLink size={10} /> View session</>}
          </button>
        )}
      </div>
      <div className="agent-job-identity">
        {profile && <CardAvatar agent={profile} size={44} />}
        <div className="agent-job-identity-text">
          <h1 className="agent-job-title">{title}</h1>
          {profile && (
            <div className="agent-job-subtitle">
              {kindLabel} · {profile.display_name}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab bar
// ---------------------------------------------------------------------------

function JobTabs({ tabs, active, onChange }) {
  return (
    <div className="page-tabs" role="tablist">
      {tabs.map((t) => {
        const Icon = t.icon;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active === t.id}
            className={`page-tab ${active === t.id ? "active" : ""}`}
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

function DiffPanel({ jobId, job, task, onChanged }) {
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
  // Wide mode: hides the comments sidebar so the diff body gets the
  // full width. Mirrored from the old ReviewDiff page where this
  // toggle lived. Session-only state -- the user who wants wide for
  // one file usually wants it for the whole review.
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const navigate = useNavigate();

  const refetch = useCallback(async () => {
    setLoading(true);
    try {
      const [d, c] = await Promise.all([
        api.getJobDiff(id).catch((e) => ({ error: e.message })),
        api.listDiffComments(id).catch(() => []),
      ]);
      if (d?.error) showToast(d.error, "high");
      else setDiff(d);
      setComments(c || []);
    } finally {
      setLoading(false);
    }
  }, [id]);

  // Light refetch that only re-pulls comments. Used after add-comment
  // so the diff body isn't re-rendered (which loses scroll position
  // and flashes the loading state).
  const refetchComments = useCallback(async () => {
    try {
      const c = await api.listDiffComments(id);
      setComments(c || []);
    } catch { /* ignore — leave the existing list in place */ }
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

  // Adding or mutating a comment re-renders react-diff-view's rows (a
  // new inline-marker widget appears), which knocks out the browser's
  // native scroll anchoring and would otherwise fling the page. Pin the
  // window scroll across the update so the reviewer stays exactly where
  // they were reading. Double rAF lands the restore after React paints
  // the new rows; the threshold skips a redundant scrollTo when nothing
  // moved.
  const restoreScroll = useCallback((y) => {
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        if (Math.abs(window.scrollY - y) > 1) window.scrollTo({ top: y });
      }),
    );
  }, []);

  const handleLineClick = (filePath, line, side) => {
    setNewAnchor({ filePath, line, side });
    setNewBody("");
  };
  const submitNew = async () => {
    if (!newAnchor || !newBody.trim()) return;
    const scrollY = window.scrollY;
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
      // Only the comments changed; don't re-pull the diff (which
      // re-mounts the diff body, flashes the loading state, and
      // dumps the user back at the top of the page).
      await refetchComments();
      restoreScroll(scrollY);
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
    // Post-rename, `maiko rules-relevant` writes to AgentJob.extra
    // because the agent's MAIKO_JOB_ID is what the CLI gets. We still
    // read Task.metadata.rules_considered as a fallback for pre-
    // rename worktrees whose history landed there. Merging both
    // gives the user the full audit trail across the migration.
    const history = [
      ...(job?.extra?.rules_considered || []),
      ...(task?.metadata?.rules_considered || []),
    ];
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
  }, [job, task]);

  if (loading) {
    return <div className="agent-job-loading"><PlanetSpinner size={12} /> Loading diff…</div>;
  }

  const verdict = task?.metadata?.review_verdict;
  const summary = task?.metadata?.review_summary;
  const artifact = task?.metadata?.artifact;

  // A coding agent reviewing its OWN diff will always "approve" — the
  // verdict verb is meaningless on self-authored code. Show a neutral
  // "ready for your review" instead. Review/pr_review agents are
  // judging someone else's code, so their real verdict stays.
  const isSelfDiff = job?.kind === "coding";
  const verdictClass = isSelfDiff ? "ready" : (verdict || "neutral");
  const verdictLabel = isSelfDiff
    ? "ready for your review"
    : (verdict ? verdict.replace(/_/g, " ") : null);

  return (
    <div className="agent-job-diff">
      {(verdictLabel || summary) && (
        <div className={`diff-verdict-banner verdict-${verdictClass}`}>
          {verdictLabel && <span className="verdict-chip">{verdictLabel}</span>}
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
      <div className="agent-job-diff-toolbar">
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => setSidebarHidden((v) => !v)}
          title={sidebarHidden ? "Show the comments sidebar" : "Hide the comments sidebar for a wider diff"}
        >
          {sidebarHidden ? <PanelRightOpen size={10} /> : <PanelRightClose size={10} />}
          {sidebarHidden ? " show sidebar" : " wide"}
        </button>
      </div>
      <div className={`agent-job-diff-grid ${sidebarHidden ? "wide" : ""}`}>
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
        <aside className={`agent-job-diff-sidebar ${sidebarHidden ? "is-hidden" : ""}`}>
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
                    const y = window.scrollY;
                    await api.createDiffComment(id, {
                      file_path: anchor.filePath,
                      line_number: anchor.line,
                      side: anchor.side,
                      body, parent_id: parentId, base_sha: diff?.base_sha,
                    });
                    // Comments-only change — keep the diff mounted (no
                    // loading flash) and hold the scroll position.
                    await refetchComments();
                    restoreScroll(y);
                  }}
                  onEditDraft={async (cid, body) => { const y = window.scrollY; await api.updateDiffComment(cid, { body }); await refetchComments(); restoreScroll(y); }}
                  onDeleteDraft={async (cid) => { const y = window.scrollY; await api.deleteDiffComment(cid); await refetchComments(); restoreScroll(y); }}
                  onResolve={async (cid) => { const y = window.scrollY; await api.updateDiffComment(cid, { status: "resolved" }); await refetchComments(); restoreScroll(y); }}
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
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  const el = e.target;
                  const start = el.selectionStart;
                  const end = el.selectionEnd;
                  setNewBody(el.value.slice(0, start) + "\n" + el.value.slice(end));
                  requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = start + 1; });
                  e.preventDefault();
                  return;
                }
                if (e.key === "Enter" && !e.shiftKey && newBody.trim()) {
                  e.preventDefault();
                  submitNew();
                }
              }}
              placeholder="Leave a comment… (Enter saves, Shift or Cmd+Enter for newline)"
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
  const id = jobId;
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [revising, setRevising] = useState(false);
  const [approving, setApproving] = useState(false);
  const [feedback, setFeedback] = useState("");

  const refetch = useCallback(async () => {
    setLoading(true);
    try {
      const p = await api.getJobPlan(id).catch(() => null);
      setPlan(p);
    } finally { setLoading(false); }
  }, [id]);
  useEffect(() => { refetch(); }, [refetch]);

  const handleApprove = async () => {
    if (approving) return;
    setApproving(true);
    try {
      await api.approveJobPlan(id);
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
      await api.reviseJobPlan(id, feedback);
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
        className="plan-body brief-content markdown"
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
  // Proposals (PROPOSAL: / TASK: blocks) get parsed into agent_proposal
  // memos by parse_and_apply_blocks at submission time. They surface on
  // the home memos pane, but the user is usually here on the job page
  // reading the report when they want to act on the follow-ups -- so
  // we mirror them inline above the artifact. Approve / edit / dismiss
  // route through ProposalCard exactly as they do on the home pane.
  const [proposals, setProposals] = useState([]);
  // Look up by source_task_id since that's what the parser stamps on
  // each memo. Standalone jobs (no parent task) currently can't have
  // proposals -- the parser's guard skips them -- so the fetch is a
  // no-op there.
  const sourceTaskId = job?.source_task_id || task?.id || null;
  const refetchProposals = useCallback(async () => {
    if (!sourceTaskId) {
      setProposals([]);
      return;
    }
    try {
      const rows = await api.getMemos({
        kind: "agent_proposal",
        source_task_id: sourceTaskId,
        status: ["pending", "seen"],
      });
      setProposals(Array.isArray(rows) ? rows : []);
    } catch {
      // Best-effort -- silent on transient failures; the home pane
      // is still the source of truth.
    }
  }, [sourceTaskId]);
  useEffect(() => {
    refetchProposals();
  }, [refetchProposals]);

  if (!artifact || !artifact.trim()) {
    return (
      <div className="agent-job-empty">
        {job?.status === "running"
          ? "Agent is still working -- report will appear here when they finish."
          : job?.status === "failed"
            ? `Job failed${job?.error ? `: ${job.error}` : "."}`
            : "No report on this job yet."}
      </div>
    );
  }
  return (
    <div className="agent-job-report-wrap">
      {proposals.length > 0 && (
        <div className="agent-job-report-proposals">
          <div className="agent-job-report-proposals-header">
            <Sparkles size={12} /> Proposed follow-ups
            <span className="agent-job-report-proposals-count">
              {proposals.length}
            </span>
          </div>
          {proposals.map((p) => (
            <ProposalCard
              key={p.id}
              proposal={p}
              onAction={refetchProposals}
            />
          ))}
        </div>
      )}
      <div className="agent-job-report brief-content markdown">
        <div dangerouslySetInnerHTML={{ __html: renderMarkdown(artifact) }} />
      </div>
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

function ChatPanel({ jobId, agentName }) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [userName, setUserName] = useState("");
  const endRef = useRef(null);
  // True after the first auto-scroll-to-bottom completes. Initial
  // load snaps without animation so a long chat doesn't visibly
  // scroll past every message; subsequent new messages still animate.
  const didInitialScrollRef = useRef(false);

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

  // Pull the user's configured display name once. Used to replace the
  // raw "user" sender label on outgoing chat messages.
  useEffect(() => {
    api.getConfig()
      .then((cfg) => setUserName((cfg?.user?.name || "").trim()))
      .catch(() => {});
  }, []);

  const senderLabel = (sender) => {
    if (sender === "user") return userName || "You";
    if (sender === "agent") return agentName || "Agent";
    return sender;
  };

  // Initial load + 8s poll while the panel's open.
  useEffect(() => {
    refetch();
    const t = setInterval(refetch, 8000);
    return () => clearInterval(t);
  }, [refetch]);

  // Snap to the bottom whenever the message count changes — opening
  // the panel or sending a new message lands on the latest entry.
  useEffect(() => {
    if (!endRef.current) return;
    // Skip the mount-time empty render. The effect fires once on
    // mount with messages.length === 0, before refetch() has landed
    // any rows — if we flipped didInitialScrollRef on that pass,
    // the next render (with the actual messages) would use the
    // smooth path and produce a visible scroll animation on page
    // open. Wait until there's something to scroll past.
    if (messages.length === 0) return;
    endRef.current.scrollIntoView({
      behavior: didInitialScrollRef.current ? "smooth" : "auto",
      block: "end",
    });
    didInitialScrollRef.current = true;
  }, [messages.length]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const res = await api.sendToAgent(jobId, { content: text, sender: "user" });
      const mode = res?.wake_mode;
      if (mode === "woke") showToast("Message sent. Waking the agent up.", "normal");
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
                  <span className="activity-sender">{senderLabel(m.sender)}</span>
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
            // Chat convention: plain Enter sends. Hold Shift, Cmd, or
            // Ctrl to insert a newline instead (so multi-line messages
            // are still easy).
            if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="Reply to the agent… ⇧+Enter for a new line"
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
