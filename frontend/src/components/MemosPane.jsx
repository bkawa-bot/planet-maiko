import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ClipboardCheck, FileText, GitPullRequest, Inbox, Bot, Check, X,
  Bell, HelpCircle, ExternalLink, ChevronDown, ChevronRight, Plus, Rocket,
  Sparkles, MessageCircle,
} from "@icons"; // MessageCircle still used in KIND_META.agent_message
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
import PupdateSnapshot from "./memos/PupdateSnapshot";
import RepoPickerModal from "./RepoPickerModal";
import "./RepoPickerModal.css";
import GateReviewModal from "./GateReviewModal";

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

// "Click into the agent" surfaces (Agents page + persistent pack) already
// show these as a status on the agent's row, so the memo entry is
// redundant noise. We hide them from the Memos list and toast once when
// they first appear instead — the user gets the heads-up without the
// permanent row sitting in the pane. localStorage keeps the "already
// toasted" set across reloads so reopening the app doesn't re-toast
// everything from history.
const HIDDEN_KINDS = new Set(["review", "agent_ready"]);
const TOAST_SEEN_KEY = "maiko.memos.toastedReadyIds";

function loadToastedIds() {
  try {
    const raw = localStorage.getItem(TOAST_SEEN_KEY);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw));
  } catch {
    return new Set();
  }
}

function saveToastedIds(set) {
  try {
    // Cap at 200 entries so the localStorage value can't grow unbounded.
    const arr = Array.from(set).slice(-200);
    localStorage.setItem(TOAST_SEEN_KEY, JSON.stringify(arr));
  } catch {
    /* non-fatal */
  }
}

function itemSignature(it) {
  // Memo-backed rows have memo_id; task-backed review rows don't,
  // so fall back to task_id. Either is stable enough that a row
  // doesn't re-toast on every poll.
  return it.memo_id || it.task_id || it.job_id || it.title;
}

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
  flow_gate: {
    Icon: ClipboardCheck,
    cta: "Approve",
    label: "Flow gate",
    tone: "plan",
  },
  flow_diff: {
    Icon: GitPullRequest,
    cta: "Review diff",
    label: "Ready",
    tone: "review",
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
  // When approving a job-approval memo lands a 422 with needs_input,
  // we surface a picker rather than letting the error toast win and
  // leaving the user unable to retry. The pending state holds the
  // memo id + the payload so the modal renders, and confirming
  // re-fires approveMemo with { repo_path }.
  const [repoPicker, setRepoPicker] = useState(null);
  // The flow gate the user opened to review (plan markdown + approve / request
  // changes / reject), or null.
  const [gateReview, setGateReview] = useState(null);

  const fetchQueue = async (isInitial = false) => {
    try {
      const data = await api.getReviewQueue();
      const next = data?.items || [];

      // Toast newly-arrived ready / review rows once, then suppress them
      // from the rendered list. localStorage tracks which signatures
      // we've already toasted so a refresh doesn't re-fire. On the
      // first fetch of a session we silently mark the current backlog
      // as seen — toasting 5 "ready" rows from yesterday's work the
      // moment the app loads would be noise, not signal.
      const toasted = loadToastedIds();
      let dirty = false;
      for (const it of next) {
        if (!HIDDEN_KINDS.has(it.kind)) continue;
        const sig = itemSignature(it);
        if (!sig || toasted.has(sig)) continue;
        if (!isInitial) {
          const who = it.agent_name || "an agent";
          const what = it.kind === "review" ? "diff ready" : "ready for review";
          showToast(`${who} ${what}: ${it.title || ""}`.trim(), "normal");
        }
        toasted.add(sig);
        dirty = true;
      }
      if (dirty) saveToastedIds(toasted);

      setItems(next);
    } catch {
      /* non-fatal — keep whatever was last loaded */
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchQueue(true); // initial: prime the "seen" set without toasting backlog
    api.getProfiles().then(setProfiles).catch(() => {});
    const id = setInterval(() => fetchQueue(false), POLL_MS);
    const onFocus = () => fetchQueue(false);
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

  // Drop the rows that the persistent pack / Agents page already
  // surfaces — the user toasts arrive separately when these first
  // appear. Filter here (not in fetchQueue) so the toast-tracking
  // loop above sees every newly-arrived row even when filtered out.
  const visibleItems = items.filter((it) => !HIDDEN_KINDS.has(it.kind));

  if (loading) return null;
  if (visibleItems.length === 0) return null;

  const iconFor = (item) => {
    return KIND_META[item.kind]?.Icon || FileText;
  };

  // Dismiss: the memo-backed kinds go through /memos/<id>/dismiss; the
  // legacy pupdate + AgentJob kinds have their own dismiss paths.
  // Review-diff rows are task-backed; "dismiss" for those means cancel
  // the task (stops any running agent, cleans the worktree, deletes
  // the row). Destructive, so we confirm first.
  const dismissItem = async (it) => {
    try {
      if (it.memo_id) {
        await api.dismissMemo(it.memo_id);
      } else if (it.kind === "pending_job" && it.job_id) {
        await api.cancelAgentJob(it.job_id);
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
        <span className="review-queue-count">{visibleItems.length}</span>
      </div>
      <div className="review-queue-list">
        {visibleItems.map((it) => {
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
                // 422 with needs_input → handler asked for more user
                // input. Today the only case is needs_repo (no local
                // clone for the job's scope_repo). Open the picker so
                // the user can pick a path and retry without losing
                // the memo.
                if (err.status === 422 && err.body?.needs_input === "needs_repo") {
                  setRepoPicker({
                    memoId: it.memo_id,
                    payload: err.body.payload || {},
                  });
                  return;
                }
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

          // Flow gate: a workflow paused at an approval gate. The body
          // carries the upstream plan (markdown); Approve / Reject act on
          // the gate inline (the same calls as the run-view gate), then
          // retire the memo so it stops surfacing.
          if (it.kind === "flow_gate") {
            const settle = async (verb) => {
              try {
                if (verb === "approve") {
                  await api.approveWorkflowNode(it.run_id, it.node_run_id);
                } else {
                  await api.rejectWorkflowNode(it.run_id, it.node_run_id);
                }
                if (it.memo_id) await api.dismissMemo(it.memo_id).catch(() => {});
                showToast(verb === "approve" ? "Approved 🐾" : "Rejected", "normal");
                fetchQueue();
              } catch (err) {
                showToast("Couldn't " + verb + ": " + (err.message || "unknown"), "high");
              }
            };
            return (
              <div
                key={`flow_gate:${it.memo_id}`}
                className={`review-queue-row tone-${meta.tone}`}
              >
                {renderRowIcon(it, Icon)}
                <div
                  className="review-queue-body review-queue-row-main"
                  onClick={() => setGateReview(it)}
                  role="button"
                  title="Review the plan and decide"
                >
                  <div className="review-queue-title">{it.title || "Flow gate"}</div>
                  <div className="review-queue-meta">
                    <span className="review-queue-kind">{meta.label}</span>
                    {it.timestamp && (
                      <span className="review-queue-time">
                        {relativeTime(it.timestamp)}
                      </span>
                    )}
                  </div>
                  <span className="review-queue-cta">Review and decide →</span>
                </div>
                <div className="review-queue-actions">
                  <button
                    className="btn btn-sm btn-primary"
                    onClick={(e) => { e.stopPropagation(); settle("approve"); }}
                    title="Approve the gate and continue the flow"
                  >
                    <Check size={12} /> Approve
                  </button>
                  <button
                    className="btn btn-sm btn-ghost"
                    onClick={(e) => { e.stopPropagation(); settle("reject"); }}
                    title="Reject and stop the flow here"
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
                    {hasUrl ? (
                      <a
                        href={it.route}
                        target="_blank"
                        rel="noreferrer"
                        className="review-queue-title-link"
                      >
                        {it.title || "(notification)"}
                      </a>
                    ) : (
                      it.title || "(notification)"
                    )}
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

          // skill_result rows render their body inline on expand;
          // no useful deep-link target exists yet, so the click flips
          // the <details> open instead of navigating away. The other
          // default-rendered kinds (plan, review, agent_ready,
          // agent_stuck) keep click-to-navigate.
          if (it.kind === "skill_result") {
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
          // agent_ready and agent_plan still render their body inline
          // as a <details> expand so the user can scan the summary
          // without leaving Home. agent_message and agent_stuck used
          // to do the same, plus embed an inline reply box -- now they
          // click-through to /jobs/<id>?view=chat where the read +
          // reply happens in full context against the live thread.
          const cta = it.cta_label || meta.cta;
          const hasRoute = !!it.route;
          const isAgentMessage =
            it.kind === "agent_ready" || it.kind === "agent_plan";
          const showInlineBody = isAgentMessage && !!(it.body && it.body.trim());
          const summaryLabel = "Read message";
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
                </details>
              )}
              {(it.memo_id || (it.kind === "review" && it.task_id)) && (
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
      {repoPicker && (
        <RepoPickerModal
          payload={repoPicker.payload}
          onCancel={() => setRepoPicker(null)}
          onConfirm={async (repoPath) => {
            try {
              await api.approveMemo(repoPicker.memoId, { repo_path: repoPath });
              setRepoPicker(null);
              fetchQueue();
              showToast("Approved — agent is queued.", "normal");
            } catch (err) {
              showToast(
                "Couldn't approve with that path: " + (err.message || "unknown"),
                "high",
              );
            }
          }}
        />
      )}
      {gateReview && (
        <GateReviewModal
          item={gateReview}
          onClose={() => setGateReview(null)}
          onSettled={fetchQueue}
        />
      )}
    </div>
  );
}
