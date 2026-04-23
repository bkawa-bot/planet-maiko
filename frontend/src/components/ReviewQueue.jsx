import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ClipboardCheck, FileText, GitPullRequest, Map, Inbox, Bot, Check, X,
} from "lucide-react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { relativeTime } from "../utils/dates";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import ProposalCard from "./ProposalCard";
import "./ReviewQueue.css";

/**
 * Home's canonical "waiting on your review" stack. Pairs with
 * OverviewPane — the overview is narrative-quality, this one is
 * dumb-and-exhaustive so nothing slips past the LLM's 3-item
 * truncation.
 *
 * Fetches /api/home/review-queue on mount + every 30s. Hides itself
 * when the list is empty so Home doesn't grow an empty bucket on
 * quiet days.
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
    cta: null,   // renders inline ProposalCard — no nav CTA
    label: "Proposal",
    tone: "plan",
  },
  pending_job: {
    Icon: Bot,
    cta: null,   // renders inline approve/dismiss — no nav CTA
    label: "Approval",
    tone: "plan",
  },
};


export default function ReviewQueue() {
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
    return () => clearInterval(id);
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

  return (
    <div className="review-queue frost-pane">
      <div className="review-queue-header">
        <Inbox size={12} /> Waiting on you
        <span className="review-queue-count">{items.length}</span>
      </div>
      <div className="review-queue-list">
        {items.map((it) => {
          const meta = KIND_META[it.kind] || KIND_META.review;
          const Icon = iconFor(it);

          // Proposals need their full edit/approve/dismiss form inline —
          // a click-to-navigate row doesn't cut it. Render the dedicated
          // ProposalCard component and hand it our fetch to refresh the
          // queue when the user approves or dismisses.
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

          // Pending AgentJobs land here for ask-first automations. No
          // navigation destination — the user approves or dismisses in
          // place and the queue refetches.
          if (it.kind === "pending_job") {
            const onApprove = async (e) => {
              e.stopPropagation();
              try {
                await api.approveAgentJob(it.job_id);
                fetchQueue();
              } catch (err) {
                showToast("Couldn't approve: " + (err.message || "unknown"), "high");
              }
            };
            const onDismiss = async (e) => {
              e.stopPropagation();
              try {
                await api.cancelAgentJob(it.job_id);
                fetchQueue();
              } catch (err) {
                showToast("Couldn't dismiss: " + (err.message || "unknown"), "high");
              }
            };
            return (
              <div
                key={`pending_job:${it.job_id}`}
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

          return (
            <button
              key={`${it.kind}:${it.task_id || it.job_id}`}
              className={`review-queue-row tone-${meta.tone}`}
              onClick={() => navigate(it.route)}
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
              <span className="review-queue-cta">{meta.cta} →</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
