import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ClipboardCheck, FileText, GitPullRequest, Map, Inbox, Bot, Check, X,
  Bell, HelpCircle, ExternalLink, ChevronDown, ChevronRight, Plus, Rocket,
  Sparkles, MessageCircle,
} from "lucide-react"; // MessageCircle still used in KIND_META.agent_message
import { api } from "../api/client";
import { showToast } from "./Toast";
import { relativeTime } from "../utils/dates";
import { renderMarkdown } from "../utils/markdown";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import ProposalCard from "./ProposalCard";
import CardAvatar from "./CardAvatar";
import "./ReviewQueue.css";
import "./MemosPane.css";
import ArtifactRow from "./memos/ArtifactRow";
import StuckReplyBox from "./memos/StuckReplyBox";
import PupdateSnapshot from "./memos/PupdateSnapshot";

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
  skill_result: {
    Icon: Sparkles,
    cta: null,
    label: "Skill",
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
  agent_message: {
    Icon: MessageCircle,
    cta: "Reply",
    label: "Message",
    tone: "info",
  },
};


export default function MemosPane() {
  const defaultOrg = useDefaultOrg();
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  // Profiles by id so memo rows owned by an agent render the agent's
  // CardAvatar in place of the generic kind icon. Fetched once on
  // mount; profile changes are rare, and an unresolvable id falls
  // back to the kind icon, so a stale map degrades gracefully.
  const [profiles, setProfiles] = useState([]);
  const profilesById = useMemo(
    () => Object.fromEntries(profiles.map((p) => [p.id, p])),
    [profiles],
  );

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
    api.getProfiles().then(setProfiles).catch(() => {});
    const id = setInterval(fetchQueue, POLL_MS);
    const onFocus = () => fetchQueue();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  // Avatar in the icon slot when an agent owns the memo; kind icon
  // otherwise. Numeric size keeps the avatar visually aligned with the
  // 14px lucide icons that share the slot.
  const renderRowIcon = (it, FallbackIcon) => {
    const profile = it.agent_id ? profilesById[it.agent_id] : null;
    if (profile) {
      return (
        <div className="review-queue-icon review-queue-icon-avatar">
          <CardAvatar agent={profile} size={32} />
        </div>
      );
    }
    return (
      <div className="review-queue-icon">
        <FallbackIcon size={14} />
      </div>
    );
  };

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
                  profile={it.agent_id ? profilesById[it.agent_id] : null}
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
                {renderRowIcon(it, Icon)}
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
            // Quiet age signal: dot at 24h+, soft tint at 72h+. Only
            // applies to notifications since they're the kind that
            // tends to get ignored — proposals and ready-for-reviews
            // have stronger semantics that don't need staleness cues.
            const ageHours = it.age_seconds != null
              ? it.age_seconds / 3600
              : 0;
            const staleClass = ageHours >= 72
              ? " review-queue-row-stale-warm"
              : ageHours >= 24
                ? " review-queue-row-stale-soft"
                : "";
            return (
              <div
                key={`notification:${it.memo_id}`}
                className={`review-queue-row tone-${priorityTone} review-queue-row-notification${staleClass}`}
              >
                {renderRowIcon(it, Icon)}
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
                    onClick={async () => {
                      try {
                        const res = await api.createTaskFromMemo(it.memo_id, {});
                        showToast(`Task created: ${res.task?.title || "(untitled)"}`, "normal");
                        fetchQueue();
                      } catch (err) {
                        showToast("Couldn't create task: " + (err.message || "unknown"), "high");
                      }
                    }}
                    title="Create a task from this notification"
                  >
                    <Plus size={12} />
                  </button>
                  <button
                    className="btn btn-sm btn-ghost"
                    onClick={async () => {
                      try {
                        await api.launchAgentFromMemo(it.memo_id, {});
                        showToast("Agent queued — check Agents tab", "normal");
                        fetchQueue();
                      } catch (err) {
                        showToast("Couldn't launch agent: " + (err.message || "unknown"), "high");
                      }
                    }}
                    title="Launch an investigation agent on this"
                  >
                    <Rocket size={12} />
                  </button>
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

          // job_artifact and skill_result rows render their body
          // inline on expand — no useful deep-link target exists yet,
          // so the click flips the <details> open instead of
          // navigating away. The other default-rendered kinds (plan,
          // review, agent_ready, agent_stuck) keep click-to-navigate.
          // Both kinds share ArtifactRow because they're conceptually
          // the same thing ("agent did a thing, here's the output").
          if (it.kind === "job_artifact" || it.kind === "skill_result") {
            return (
              <ArtifactRow
                key={`${it.kind}:${it.memo_id || it.job_id}`}
                it={it}
                meta={meta}
                Icon={Icon}
                profile={it.agent_id ? profilesById[it.agent_id] : null}
                onDismiss={() => dismissItem(it)}
                defaultOrg={defaultOrg}
              />
            );
          }

          // Default row: click-to-navigate + dismiss X. Covers plan,
          // review, agent_ready, agent_stuck, agent_message, plus
          // anything the catchall memo path surfaces.
          //
          // Agent message rows render the body inline as a
          // expand-on-click <details>. Stuck and user-directed
          // (agent_message) rows additionally embed the inline
          // reply box so the user can answer without leaving Home.
          const cta = it.cta_label || meta.cta;
          const hasRoute = !!it.route;
          const isAgentMessage =
            it.kind === "agent_ready" ||
            it.kind === "agent_stuck" ||
            it.kind === "agent_plan" ||
            it.kind === "agent_message";
          const showInlineBody = isAgentMessage && !!(it.body && it.body.trim());
          const showInlineReply =
            (it.kind === "agent_stuck" || it.kind === "agent_message")
            && !!(it.thread_id || it.task_id);
          const replyTargetId =
            it.thread_id || it.task_id || it.job_id || null;
          // Summary label per kind. agent_message reuses the
          // stuck wording — same interface, same affordance.
          const summaryLabel =
            (it.kind === "agent_stuck" || it.kind === "agent_message")
              ? "Read & reply"
              : "Read message";
          return (
            <div
              key={`${it.kind}:${it.task_id || it.job_id || it.memo_id}`}
              className={`review-queue-row tone-${meta.tone}${showInlineBody ? " review-queue-row-with-body" : ""}`}
            >
              <button
                type="button"
                className="review-queue-row-main"
                onClick={() => hasRoute && navigate(it.route)}
                disabled={!hasRoute}
              >
                {renderRowIcon(it, Icon)}
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
              {showInlineBody && (
                <details
                  className="review-queue-message-body"
                  onClick={(e) => e.stopPropagation()}
                >
                  <summary>
                    <ChevronRight size={10} className="review-queue-message-chevron" />
                    {summaryLabel}
                  </summary>
                  <div
                    className="markdown"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(it.body) }}
                  />
                  {showInlineReply && (
                    <StuckReplyBox
                      taskId={replyTargetId}
                      memoId={it.memo_id}
                      onReplied={fetchQueue}
                    />
                  )}
                </details>
              )}
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
