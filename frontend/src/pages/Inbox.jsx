import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import PupdateCard from "../components/PupdateCard";
import { renderMarkdown } from "../utils/markdown";
import { formatTime } from "../utils/dates";
import { ExternalLink, X, Inbox as InboxIcon, ClipboardCheck, CheckSquare, MoreHorizontal, MessageCircle, Pencil, GitBranch, Calendar as CalendarIcon, Bot, Lightbulb, AlertTriangle, MessageSquare, Play, Loader, FileText, Folder, AlertCircle, Eye } from "lucide-react";
import ProposalCard from "../components/ProposalCard";
import "./Inbox.css";
import "./Brainstorm.css";
import "./Suggestions.css";

// Tabs below the Action strip — they show the Activity feed, so
// action-category pupdates are excluded (the strip owns them).
const notAction = (p) => p.category !== "action";
const TABS = [
  { id: "all", label: "All", filter: (p) => p.type !== "approval" && p.type !== "suggestion" && notAction(p) },
  { id: "prs", label: "PRs", filter: (p) => p.type?.startsWith("pr_") && notAction(p) },
  { id: "calendar", label: "Calendar", filter: (p) => p.source === "calendar" },
  { id: "from_maiko", label: "From Maiko", filter: (p) => (p.source === "maiko" || p.source === "agent" || p.type === "suggestion" || p.type === "approval") && notAction(p) },
  { id: "system", label: "System", filter: (p) => (p.source === "system" || p.source === "scheduler") && notAction(p) },
];

const PRIORITY_ORDER = { urgent: 0, high: 1, normal: 2, low: 3 };

const SOURCE_ICONS = {
  github: GitBranch,
  linear: CheckSquare,
  calendar: CalendarIcon,
  slack: MessageSquare,
  maiko: Lightbulb,
  agent: Bot,
};

function sourceIcon(source) {
  const Icon = SOURCE_ICONS[source] || InboxIcon;
  return <Icon size={14} />;
}

export default function Inbox() {
  const [pupdates, setPupdates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("all");
  const [expanded, setExpanded] = useState(null);
  const [moreMenu, setMoreMenu] = useState(null);
  const [focus, setFocus] = useState(null);
  const [reviewResult, setReviewResult] = useState(null);
  const [reviewing, setReviewing] = useState(null);
  const [brainStatus, setBrainStatus] = useState(null);
  const [schedule, setSchedule] = useState(null);
  const [inProgressCount, setInProgressCount] = useState(0);

  const fetchPupdates = async () => {
    setLoading(true);
    try {
      const [data, foc, brain, sched, ip] = await Promise.all([
        api.getPupdates(),
        api.getFocus().catch(() => null),
        api.getBrainStatus().catch(() => null),
        api.getSchedule().catch(() => null),
        api.getTasks({ status: "in_progress" }).catch(() => []),
      ]);
      data.sort((a, b) => (PRIORITY_ORDER[a.priority] ?? 99) - (PRIORITY_ORDER[b.priority] ?? 99));
      setPupdates(data);
      setFocus(foc);
      setBrainStatus(brain);
      setSchedule(sched);
      setInProgressCount(ip.length);
    } catch (err) { console.error(err); }
    setLoading(false);
  };

  useEffect(() => { fetchPupdates(); }, []);

  const handleDismiss = async (e, id) => {
    e.stopPropagation();
    await api.dismissPupdate(id);
    setPupdates((prev) => prev.filter((p) => p.id !== id));
    if (expanded === id) setExpanded(null);
  };

  const handleMarkRead = async (id) => {
    await api.markRead(id);
    setPupdates((prev) => prev.map((p) => (p.id === id ? { ...p, read: true } : p)));
  };

  const toggleExpand = (p) => {
    if (expanded === p.id) {
      setExpanded(null);
    } else {
      setExpanded(p.id);
      if (!p.read) handleMarkRead(p.id);
    }
  };

  const handleReviewPR = async (p) => {
    setReviewing(p.id);
    showToast("Maiko is reviewing the PR...", "normal");
    try {
      const repo = p.metadata?.repo || "";
      const number = p.metadata?.number || "";
      const result = await api.runSkill("investigate", {
        context: {
          query: `Review PR #${number} in ${repo}: ${p.title}`,
          context: `URL: ${p.url || ""}\n${p.body || ""}`,
          pupdates: "[]", tasks: "[]", calendar: "[]",
        },
      });
      // Check for permission/tool blocked errors in the output
      const output = result.output || result.error || "";
      const blockedMatch = output.match(/blocked by permissions|permission.*denied|not allowed|allowedTools/i);
      const toolMatch = output.match(/`?(Bash|WebFetch|WebSearch|mcp__\w+|gh)`?/g);
      if (!result.success && (blockedMatch || toolMatch)) {
        const tools = toolMatch ? [...new Set(toolMatch.map(t => t.replace(/`/g, "")))].join(", ") : "Bash, WebFetch";
        setReviewResult({
          pupdate: p,
          success: false,
          output: `The agent needs tool permissions to review this PR.\n\nAdd these to **Settings > Agent Preferences > Allowed Tools**:\n\n\`${tools}\`\n\nThen try again.`,
        });
        showToast("Agent needs tool permissions — check the review panel", "high");
      } else {
        setReviewResult({ pupdate: p, ...result });
        showToast(result.success ? "Review ready!" : "Couldn't review", result.success ? "normal" : "high");
      }
    } catch (err) {
      setReviewResult({
        pupdate: p,
        success: false,
        output: `Review failed: ${err.message}\n\nMake sure the backend is running and Claude Code is installed.`,
      });
      showToast("Review failed: " + err.message, "high");
    }
    setReviewing(null);
  };

  const currentFilter = TABS.find((t) => t.id === tab)?.filter || (() => true);
  const filtered = pupdates.filter(currentFilter);
  const actionItems = pupdates.filter((p) => p.category === "action");

  const tabCounts = TABS.reduce((acc, t) => {
    acc[t.id] = pupdates.filter(t.filter).length;
    return acc;
  }, {});

  return (
    <div className="inbox">
    <div className="inbox-grid">
      <div className="inbox-main">
      {actionItems.length > 0 && (
        <div className="action-strip">
          <div className="action-strip-header">
            <AlertCircle size={12} />
            <span>Needs your hands</span>
            <span className="action-strip-count">{actionItems.length}</span>
          </div>
          <div className="action-strip-list">
            {actionItems.map((p) => (
              p.type === "agent_proposal" ? (
                <ProposalCard key={p.id} proposal={p} onAction={fetchPupdates} />
              ) : (
                <PupdateCard
                  key={p.id}
                  pupdate={p}
                  isExpanded={expanded === p.id}
                  onToggleExpand={() => toggleExpand(p)}
                  onMarkRead={handleMarkRead}
                  onDismiss={handleDismiss}
                  onReviewPR={handleReviewPR}
                  reviewing={reviewing}
                  sourceIcon={sourceIcon}
                />
              )
            ))}
          </div>
        </div>
      )}
      <div className="inbox-tab-bar">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`inbox-tab ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
        {filtered.length > 0 && tab !== "from_maiko" && (
          <button
            className="btn btn-sm btn-danger"
            style={{ marginLeft: "auto" }}
            onClick={async () => {
              for (const p of filtered) await api.dismissPupdate(p.id);
              fetchPupdates();
            }}
          >
            Dismiss All
          </button>
        )}
      </div>

      {tab === "from_maiko" ? (
        <div style={{ padding: "8px 0" }}>
          {filtered.length === 0 ? (
            <div className="empty-state">
              <InboxIcon size={36} className="empty-icon" />
              <div className="empty-title">Nothing from Maiko yet</div>
              <div className="empty-sub">Maiko posts brainstorms, approvals, and investigations here as they come up.</div>
            </div>
          ) : (
            <div className="card-list card-list-container">
              {filtered.map((p) => (
                p.type === "agent_proposal" ? (
                  <ProposalCard key={p.id} proposal={p} onAction={fetchPupdates} />
                ) : (
                  <PupdateCard
                    key={p.id}
                    pupdate={p}
                    isExpanded={expanded === p.id}
                    onToggleExpand={() => toggleExpand(p)}
                    onMarkRead={handleMarkRead}
                    onDismiss={handleDismiss}
                    onReviewPR={handleReviewPR}
                    reviewing={reviewing}
                    sourceIcon={sourceIcon}
                    showQuickDismiss={true}
                  />
                )
              ))}
            </div>
          )}
        </div>
      ) : loading ? (
        <p className="page-empty">Loading...</p>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          <InboxIcon size={36} className="empty-icon" />
          <div className="empty-title">Nothing here</div>
          <div className="empty-sub">All clear in this category!</div>
        </div>
      ) : (
        <div className="card-list card-list-container">
          {filtered.map((p) => (
            <PupdateCard
              key={p.id}
              pupdate={p}
              isExpanded={expanded === p.id}
              onToggleExpand={() => toggleExpand(p)}
              onMarkRead={handleMarkRead}
              onDismiss={handleDismiss}
              onReviewPR={handleReviewPR}
              reviewing={reviewing}
              sourceIcon={sourceIcon}
            />
          ))}
        </div>
      )}
      </div>

      {/* Sidebar */}
      <div className="inbox-sidebar">
        <div className="home-widget">
          <div className="widget-header"><CheckSquare size={12} /> Focus</div>
          {schedule?.blocks?.length > 0 && schedule.blocks[0].tasks?.length > 0 ? (
            <div className="sidebar-task-list">
              {schedule.blocks[0].tasks.slice(0, 4).map((t) => {
                const dotColor = {
                  new: "var(--text-muted)", in_progress: "#60a5fa", waiting: "#fbbf24",
                  review: "#a78bfa", done: "#4ade80", blocked: "#d1a050",
                }[t.status] || "var(--text-muted)";
                return (
                  <div key={t.id} className="sidebar-task">
                    <span className="sidebar-task-dot" style={{ background: dotColor }} />
                    <span className="sidebar-task-title">{t.title}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="widget-empty">No focus tasks scheduled</div>
          )}
        </div>
        <div className="home-widget">
          <div className="widget-header">At a Glance</div>
          <div className="sidebar-stats">
            <div className="sidebar-stat">
              <span className="sidebar-stat-val" style={{ color: "var(--pink)" }}>{pupdates.filter(p => !p.read).length}</span>
              <span className="sidebar-stat-label">Unread</span>
            </div>
            <div className="sidebar-stat">
              <span className="sidebar-stat-val" style={{ color: "var(--blue)" }}>{inProgressCount}</span>
              <span className="sidebar-stat-label">In Progress</span>
            </div>
            <div className="sidebar-stat">
              <span className="sidebar-stat-val" style={{ color: "var(--urgent)" }}>{pupdates.filter(p => p.priority === "urgent" || p.priority === "high").length}</span>
              <span className="sidebar-stat-label">Urgent</span>
            </div>
          </div>
        </div>
        <div className="home-widget">
          <div className="widget-header"><span className="sidebar-brain-dot" /> Brain</div>
          <div className="widget-detail">
            <span>Cycles: {brainStatus?.cycle_count || 0}</span>
            <span>Last: {brainStatus?.last_cycle ? formatTime(brainStatus.last_cycle) : "Never"}</span>
          </div>
        </div>
      </div>
    </div>

    {/* PR Review Modal */}
    {reviewResult && (
      <div className="modal-overlay" onClick={() => setReviewResult(null)}>
        <div className="review-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-header">
            <Eye size={14} />
            <span>PR Review</span>
            <span style={{ fontWeight: 400, fontSize: 12, color: "var(--text-muted)", marginLeft: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {reviewResult.pupdate?.title}
            </span>
            <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
              {reviewResult.pupdate?.url && (
                <a href={reviewResult.pupdate.url} target="_blank" rel="noreferrer" className="btn btn-sm">
                  <ExternalLink size={10} /> Open PR
                </a>
              )}
              <button className="btn btn-sm" onClick={() => setReviewResult(null)}>
                <X size={10} />
              </button>
            </div>
          </div>
          <div className="modal-body">
            {reviewResult.success ? (
              <div className="review-content" dangerouslySetInnerHTML={{ __html: renderMarkdown(reviewResult.output) }} />
            ) : (
              <div style={{ color: "var(--urgent)", fontSize: 13 }}>
                {reviewResult.error || "Review failed"}
              </div>
            )}
          </div>
        </div>
      </div>
    )}
    </div>
  );
}
