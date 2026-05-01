import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ClipboardCheck, FileText, GitPullRequest, Map, Inbox, Bot, Check, X,
  Bell, HelpCircle, ExternalLink, ChevronDown, ChevronRight,
} from "lucide-react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { relativeTime } from "../utils/dates";
import { renderMarkdown } from "../utils/markdown";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import ProposalCard from "./ProposalCard";
import "./ReviewQueue.css";
import "./MemosPane.css";

/**
 * Home's unified Memos pane — the single surface for every
 * persistent user-facing item. Replaces ReviewQueue (things waiting
 * on your review) and NotificationsPane (info-only asks).
 *
 * Reads /api/home/review-queue, which aggregates review tasks,
 * agent_plan / agent_proposal / agent_ready / agent_stuck / job_approval
 * memos, notification memos, and standalone AgentJob artifacts.
 *
 * Polls every 30s. Auto-hides when empty so Home doesn't grow an
 * empty bucket on quiet days.
 */

const POLL_MS = 30_000;

const KIND_META = {
  plan: {
    Icon: ClipboardCheck,
    cta: "Review plan",
    label: "Plan",
    tone: "plan",
  },
  review: {
    Icon: GitPullRequest,
    cta: "Review diff",
    label: "Diff",
    tone: "review",
  },
  job_artifact: {
    Icon: FileText,
    cta: "Open report",
    label: "Report",
    tone: "artifact",
  },
  proposal: {
    Icon: ClipboardCheck,
    cta: null,
    label: "Proposal",
    tone: "plan",
  },
  pending_job: {
    Icon: Bot,
    cta: null,
    label: "Approval",
    tone: "plan",
  },
  notification: {
    Icon: Bell,
    cta: null,
    label: "Notification",
    tone: "info",
  },
  agent_ready: {
    Icon: GitPullRequest,
    cta: "Review diff",
    label: "Ready",
    tone: "review",
  },
  agent_stuck: {
    Icon: HelpCircle,
    cta: "Help out",
    label: "Stuck",
    tone: "stuck",
  },
};


export default function MemosPane() {
  const defaultOrg = useDefaultOrg();
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchQueue = async () => {
    try {
      const data = await api.getReviewQueue();
      setItems(data?.items || []);
    } catch {
      /* non-fatal — keep whatever was last loaded */
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchQueue();
    const id = setInterval(fetchQueue, POLL_MS);
    const onFocus = () => fetchQueue();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  if (loading) return null;
  if (items.length === 0) return null;

  // Cartograph artifacts look enough like a Map that we lean into it;
  // otherwise stick with the kind default.
  const iconFor = (item) => {
    if (item.kind === "job_artifact" && item.title?.toLowerCase().includes("cartograph")) {
      return Map;
    }
    return KIND_META[item.kind]?.Icon || FileText;
  };

  // Dismiss: the memo-backed kinds go through /memos/<id>/dismiss; the
  // legacy pupdate + AgentJob kinds have their own dismiss paths.
  // Review-diff rows are task-backed — "dismiss" for those means cancel
  // the task (stops any running agent, cleans the worktree, deletes
  // the row). Destructive, so we confirm first. Job-artifact rows are
  // "I've seen this report" — flip extra.reviewed=true on the job so
  // the home_api filter drops it from the pane.
  const dismissItem = async (it) => {
    try {
      if (it.memo_id) {
        await api.dismissMemo(it.memo_id);
      } else if (it.kind === "pending_job" && it.job_id) {
        await api.cancelAgentJob(it.job_id);
      } else if (it.kind === "job_artifact" && it.job_id) {
        await api.ackAgentJob(it.job_id);
      } else if (it.kind === "review" && it.task_id) {
        const ok = window.confirm(
          `Cancel "${it.title || "this task"}"? Stops any running agent and discards the diff.`
        );
        if (!ok) return;
        await api.cancelTask(it.task_id);
      } else {
        return;
      }
      fetchQueue();
    } catch (err) {
      showToast("Couldn't dismiss: " + (err.message || "unknown"), "high");
    }
  };

  return (
    <div className="review-queue memos-pane frost-pane">
      <div className="review-queue-header">
        <Inbox size={12} /> Memos
        <span className="review-queue-count">{items.length}</span>
      </div>
      <div className="review-queue-list">
        {items.map((it) => {
          const meta = KIND_META[it.kind] || KIND_META.review;
          const Icon = iconFor(it);

          // Proposals render inline via the dedicated ProposalCard so
          // the edit/approve/dismiss flow stays in one place.
          if (it.kind === "proposal" && it.proposal) {
            return (
              <div
                key={`proposal:${it.proposal.id}`}
                className="review-queue-proposal"
              >
                <ProposalCard
                  proposal={it.proposal}
                  onAction={fetchQueue}
                />
              </div>
            );
          }

          // Ask-first approval rows. New memo-backed entries approve
          // through /memos/<id>/approve (handler mints the real
          // AgentJob). Legacy pending_approval AgentJobs still exist
          // in the DB — those approve through the old endpoint.
          if (it.kind === "pending_job") {
            const onApprove = async (e) => {
              e.stopPropagation();
              try {
                if (it.memo_id) {
                  await api.approveMemo(it.memo_id);
                } else {
                  await api.approveAgentJob(it.job_id);
                }
                fetchQueue();
              } catch (err) {
                showToast("Couldn't approve: " + (err.message || "unknown"), "high");
              }
            };
            const onDismiss = async (e) => {
              e.stopPropagation();
              try {
                if (it.memo_id) {
                  await api.dismissMemo(it.memo_id);
                } else {
                  await api.cancelAgentJob(it.job_id);
                }
                fetchQueue();
              } catch (err) {
                showToast("Couldn't dismiss: " + (err.message || "unknown"), "high");
              }
            };
            return (
              <div
                key={`pending_job:${it.memo_id || it.job_id}`}
                className={`review-queue-row tone-${meta.tone} review-queue-row-pending`}
              >
                <div className="review-queue-icon"><Icon size={14} /></div>
                <div className="review-queue-body">
                  <div className="review-queue-title">
                    {it.title || "(untitled)"}
                  </div>
                  <div className="review-queue-meta">
                    <span className="review-queue-kind">{meta.label}</span>
                    {it.job_kind && (
                      <span className="review-queue-subkind">{it.job_kind}</span>
                    )}
                    {it.repo && (
                      <span className="review-queue-repo" title={it.repo}>
                        {formatRepo(it.repo, defaultOrg)}
                      </span>
                    )}
                    {it.timestamp && (
                      <span className="review-queue-time">
                        {relativeTime(it.timestamp)}
                      </span>
                    )}
                  </div>
                  {it.description && (
                    <div className="review-queue-description">{it.description}</div>
                  )}
                  {it.pupdate_snapshot && (
                    <PupdateSnapshot snap={it.pupdate_snapshot} />
                  )}
                </div>
                <div className="review-queue-actions">
                  <button
                    className="btn btn-sm btn-primary"
                    onClick={onApprove}
                    title="Approve and queue the agent job"
                  >
                    <Check size={12} /> Approve
                  </button>
                  <button
                    className="btn btn-sm btn-ghost"
                    onClick={onDismiss}
                    title="Dismiss without running"
                  >
                    <X size={12} />
                  </button>
                </div>
              </div>
            );
          }

          // Notifications: info-only memos. Click-through to url if
          // set, dismiss X to remove. Body rendered inline as markdown
          // so longer notifications (from notify_me with templates) stay
          // readable without navigating away.
          if (it.kind === "notification") {
            const hasUrl = !!it.route;
            const priorityTone = it.priority === "urgent"
              ? "urgent"
              : it.priority === "high"
                ? "high"
                : it.priority === "low"
                  ? "low"
                  : "info";
            return (
              <div
                key={`notification:${it.memo_id}`}
                className={`review-queue-row tone-${priorityTone} review-queue-row-notification`}
              >
                <div className="review-queue-icon"><Icon size={14} /></div>
                <div className="review-queue-body">
                  <div className="review-queue-title">
                    {it.title || "(notification)"}
                  </div>
                  {it.body && (
                    <div
                      className="review-queue-description markdown"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(it.body) }}
                    />
                  )}
                  {it.pupdate_snapshot && (
                    <PupdateSnapshot snap={it.pupdate_snapshot} />
                  )}
                  <div className="review-queue-meta">
                    {it.priority && it.priority !== "normal" && (
                      <span className="review-queue-kind">{it.priority}</span>
                    )}
                    {it.timestamp && (
                      <span className="review-queue-time">
                        {relativeTime(it.timestamp)}
                      </span>
                    )}
                  </div>
                </div>
                <div className="review-queue-actions">
                  {hasUrl && (
                    <a
                      href={it.route}
                      target="_blank"
                      rel="noreferrer"
                      className="btn btn-sm"
                      title="Open source"
                    >
                      <ExternalLink size={10} />
                    </a>
                  )}
                  <button
                    className="btn btn-sm btn-ghost"
                    onClick={() => dismissItem(it)}
                    title="Dismiss"
                  >
                    <X size={12} />
                  </button>
                </div>
              </div>
            );
          }

          // Default row: click-to-navigate + dismiss X. Covers plan,
          // review, agent_ready, agent_stuck, job_artifact.
          const cta = it.cta_label || meta.cta;
          const hasRoute = !!it.route;
          return (
            <div
              key={`${it.kind}:${it.task_id || it.job_id || it.memo_id}`}
              className={`review-queue-row tone-${meta.tone}`}
            >
              <button
                type="button"
                className="review-queue-row-main"
                onClick={() => hasRoute && navigate(it.route)}
                disabled={!hasRoute}
              >
                <div className="review-queue-icon"><Icon size={14} /></div>
                <div className="review-queue-body">
                  <div className="review-queue-title">
                    {it.title || "(untitled)"}
                  </div>
                  <div className="review-queue-meta">
                    <span className="review-queue-kind">{meta.label}</span>
                    {it.repo && (
                      <span className="review-queue-repo" title={it.repo}>
                        {formatRepo(it.repo, defaultOrg)}
                      </span>
                    )}
                    {it.agent_name && (
                      <span className="review-queue-agent">by {it.agent_name}</span>
                    )}
                    {it.timestamp && (
                      <span className="review-queue-time">
                        {relativeTime(it.timestamp)}
                      </span>
                    )}
                  </div>
                </div>
                {hasRoute && cta && (
                  <span className="review-queue-cta">{cta} →</span>
                )}
              </button>
              {(it.memo_id || (it.kind === "review" && it.task_id) || (it.kind === "job_artifact" && it.job_id)) && (
                <button
                  className="btn btn-sm btn-ghost memos-pane-dismiss"
                  onClick={() => dismissItem(it)}
                  title={
                    it.kind === "review" && !it.memo_id
                      ? "Cancel task"
                      : "Dismiss"
                  }
                >
                  <X size={12} />
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}


/** Compact "triggered by" card — shows the pupdate that fired the
 *  automation so the user has context without clicking through.
 *  Collapsed by default; clicks expand to show the body. */
function PupdateSnapshot({ snap }) {
  const [open, setOpen] = useState(false);
  if (!snap) return null;
  const hasBody = !!(snap.body && snap.body.trim());
  return (
    <div className="pupdate-snapshot">
      <button
        type="button"
        className="pupdate-snapshot-head"
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        title={hasBody ? "Show triggering pupdate" : "Triggering pupdate"}
      >
        {hasBody && (open ? <ChevronDown size={10} /> : <ChevronRight size={10} />)}
        <span className="pupdate-snapshot-label">Triggered by</span>
        {snap.source && <span className="pupdate-snapshot-tag">{snap.source}</span>}
        {snap.type && <span className="pupdate-snapshot-tag">{snap.type}</span>}
        <span className="pupdate-snapshot-title">{snap.title}</span>
        {snap.url && (
          <a
            href={snap.url}
            target="_blank"
            rel="noreferrer"
            className="pupdate-snapshot-link"
            onClick={(e) => e.stopPropagation()}
            title="Open source"
          >
            <ExternalLink size={9} />
          </a>
        )}
      </button>
      {open && hasBody && (
        <div
          className="pupdate-snapshot-body markdown"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(snap.body) }}
        />
      )}
    </div>
  );
}
