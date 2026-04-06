import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { ExternalLink, X, Eye, Search, Inbox as InboxIcon, ClipboardCheck, FolderKanban, CheckSquare, MoreHorizontal, MessageCircle, Pencil, GitBranch, Calendar as CalendarIcon, Bot, Lightbulb, AlertTriangle, MessageSquare, ChevronRight, Brain, Play, Loader, RefreshCw, FileText, Folder } from "lucide-react";
import "./Inbox.css";
import "./Brainstorm.css";
import "./Suggestions.css";

const TABS = [
  { id: "all", label: "All", filter: (p) => p.type !== "approval" && p.type !== "suggestion" },
  { id: "prs", label: "PRs", filter: (p) => p.type?.startsWith("pr_") },
  { id: "calendar", label: "Calendar", filter: (p) => p.source === "calendar" },
  { id: "from_maiko", label: "From Maiko", filter: (p) => p.source === "maiko" || p.source === "agent" || p.type === "suggestion" || p.type === "approval" },
  { id: "system", label: "System", filter: (p) => p.source === "system" || p.source === "scheduler" },
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
  const [tasks, setTasks] = useState([]);

  // Brainstorm state
  const [bsResult, setBsResult] = useState(null);
  const [bsRunning, setBsRunning] = useState(false);
  const [bsLastRun, setBsLastRun] = useState(null);
  const [scanning, setScanning] = useState(false);

  const runBrainstorm = async () => {
    setBsRunning(true);
    setBsResult(null);
    showToast("Maiko is thinking...", "normal");
    try {
      const res = await api.runSkill("brainstorm", {
        context: {
          pupdates: JSON.stringify(pupdates.slice(0, 20), null, 2),
          tasks: JSON.stringify(tasks.slice(0, 20), null, 2),
        },
      });
      setBsResult(res);
      setBsLastRun(new Date());
      showToast(res.success ? "Brainstorm complete!" : "Brainstorm had trouble", res.success ? "normal" : "high");
    } catch (err) {
      setBsResult({ success: false, error: err.message, output: "" });
      showToast("Something went wrong", "high");
    }
    setBsRunning(false);
  };

  const handleScan = async () => {
    setScanning(true);
    try {
      await api.runScan();
      await fetchPupdates();
    } catch (err) { console.error(err); }
    setScanning(false);
  };

  const fetchPupdates = async () => {
    setLoading(true);
    try {
      const [data, foc, brain, t] = await Promise.all([
        api.getPupdates(),
        api.getFocus().catch(() => null),
        api.getBrainStatus().catch(() => null),
        api.getTasks({ status: "in_progress" }).catch(() => []),
      ]);
      data.sort((a, b) => (PRIORITY_ORDER[a.priority] ?? 99) - (PRIORITY_ORDER[b.priority] ?? 99));
      setPupdates(data);
      setFocus(foc);
      setBrainStatus(brain);
      setTasks(t);
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

  const tabCounts = TABS.reduce((acc, t) => {
    acc[t.id] = pupdates.filter(t.filter).length;
    return acc;
  }, {});

  return (
    <div className="inbox">
    <div className="inbox-grid">
      <div className="inbox-main">
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
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <button className="btn btn-sm" onClick={handleScan} disabled={scanning}>
              <RefreshCw size={10} className={scanning ? "spin" : ""} /> {scanning ? "Scanning..." : "Run Scan"}
            </button>
            <button className="btn btn-sm" onClick={async () => {
              setBsRunning(true);
              setBsResult(null);
              showToast("Maiko is thinking...", "normal");
              try {
                const res = await api.runSkill("brainstorm", {
                  context: {
                    pupdates: JSON.stringify(pupdates.slice(0, 20), null, 2),
                    tasks: JSON.stringify(tasks.slice(0, 20), null, 2),
                  },
                });
                setBsResult(res);
                setBsLastRun(new Date());
                if (res.success) {
                  await api.createPupdate({
                    id: "bs-" + Date.now(),
                    source: "maiko",
                    type: "brainstorm_result",
                    title: "Brainstorm Results",
                    body: res.output,
                    priority: "normal",
                  });
                  await fetchPupdates();
                  showToast("Brainstorm complete!", "normal");
                } else {
                  showToast("Brainstorm had trouble", "high");
                }
              } catch (err) {
                setBsResult({ success: false, error: err.message, output: "" });
                showToast("Something went wrong", "high");
              }
              setBsRunning(false);
            }} disabled={bsRunning}>
              <Brain size={10} /> {bsRunning ? "Running..." : "Run Brainstorm"}
            </button>
          </div>
          {filtered.length === 0 ? (
            <div className="empty-state">
              <InboxIcon size={36} className="empty-icon" />
              <div className="empty-title">Nothing from Maiko yet</div>
              <div className="empty-sub">Run a scan or brainstorm to get suggestions, approvals, and ideas</div>
            </div>
          ) : (
            <div className="card-list card-list-container">
              {filtered.map((p) => (
                <div
                  key={p.id}
                  className={`card pupdate-card ${p.priority} ${p.read ? "read" : ""} ${expanded === p.id ? "expanded" : ""}`}
                  onClick={() => toggleExpand(p)}
                >
                  <div className="card-left-bar" />
                  <div className="card-source-icon">
                    {sourceIcon(p.source)}
                  </div>
                  <div className="card-content">
                    <div className="card-top">
                      <span className="card-source">{p.source}</span>
                      <span className="card-title">
                        {p.url ? <a href={p.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{p.title}</a> : p.title}
                      </span>
                    </div>
                    <div className="card-meta">
                      <span className="card-type">{p.type?.replace(/_/g, " ")}</span>
                      <span className="card-time">{new Date(p.timestamp).toLocaleString()}</span>
                      {p.actionable && <button className="card-action-hint" onClick={(e) => { e.stopPropagation(); toggleExpand(p); }}>{p.action_hint}</button>}
                    </div>

                    {expanded === p.id && p.body && (
                      <div className="rich-body">{p.body}</div>
                    )}

                    {expanded === p.id && (
                      <div className="card-inline-actions" onClick={(e) => e.stopPropagation()}>
                        {p.type === "pr_review_requested" && (
                          <button className="btn btn-sm btn-action" onClick={() => handleReviewPR(p)} disabled={reviewing === p.id}>
                            <Eye size={10} /> {reviewing === p.id ? "Reviewing..." : "Review PR"}
                          </button>
                        )}
                        {(p.type === "pr_ci_failed" || p.type === "incident") && (
                          <button className="btn btn-sm btn-session"><Search size={10} /> Investigate</button>
                        )}
                        {p.type !== "suggestion" && (
                          <button className="btn btn-sm btn-create" onClick={async () => {
                            try {
                              await api.createTask({
                                id: `task-${p.id}`,
                                title: p.title,
                                type: "todo",
                                priority: p.priority,
                                source_pupdate_id: p.id,
                                url: p.url || "",
                                tags: p.tags || [],
                              });
                              showToast(`Task created: ${p.title.slice(0, 40)}...`, "normal");
                              handleMarkRead(p.id);
                            } catch (err) {
                              showToast("Couldn't create task", "high");
                            }
                          }}><CheckSquare size={10} /> Create Task</button>
                        )}
                        <button className="btn btn-sm btn-approve" onClick={async () => {
                          try {
                            await api.createProject({
                              id: `proj-${p.id}`,
                              title: p.title,
                              description: p.body || "",
                              priority: p.priority || "normal",
                            });
                            showToast(`Project created: ${p.title.slice(0, 40)}...`, "normal");
                            handleMarkRead(p.id);
                          } catch (err) {
                            showToast("Couldn't create project", "high");
                          }
                        }}><FolderKanban size={10} /> Create Project</button>
                        <button className="btn btn-sm btn-danger" onClick={(e) => handleDismiss(e, p.id)}>
                          <X size={10} /> Dismiss
                        </button>
                        {p.tags?.length > 0 && (
                          <div className="card-tags-inline">
                            {p.tags.map((t) => <span key={t} className="tag">{t}</span>)}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="card-right">
                    <button className="btn-ghost btn-dismiss-quick" onClick={(e) => handleDismiss(e, p.id)} title="Dismiss"><X size={12} /></button>
                    <span className={`card-priority badge ${p.priority}`}>{p.priority}</span>
                    <ChevronRight size={14} className={`card-chevron ${expanded === p.id ? "open" : ""}`} />
                  </div>
                </div>
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
            <div
              key={p.id}
              className={`card pupdate-card ${p.priority} ${p.read ? "read" : ""} ${expanded === p.id ? "expanded" : ""}`}
              onClick={() => toggleExpand(p)}
            >
              <div className="card-left-bar" />
              <div className="card-source-icon">
                {sourceIcon(p.source)}
              </div>
              <div className="card-content">
                <div className="card-top">
                  <span className="card-source">{p.source}</span>
                  <span className="card-title">
                    {p.url ? <a href={p.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{p.title}</a> : p.title}
                  </span>
                </div>
                <div className="card-meta">
                  <span className="card-type">{p.type?.replace(/_/g, " ")}</span>
                  <span className="card-time">{new Date(p.timestamp).toLocaleString()}</span>
                  {p.actionable && <button className="card-action-hint" onClick={(e) => { e.stopPropagation(); toggleExpand(p); }}>{p.action_hint}</button>}
                </div>

                {expanded === p.id && p.body && (
                  <div className="rich-body">{p.body}</div>
                )}

                {/* Inline actions — show when expanded */}
                {expanded === p.id && (
                  <div className="card-inline-actions" onClick={(e) => e.stopPropagation()}>
                    {p.type === "pr_review_requested" && (
                      <button className="btn btn-sm btn-action" onClick={async () => {
                        handleReviewPR(p);
                      }} disabled={reviewing === p.id}>
                        <Eye size={10} /> {reviewing === p.id ? "Reviewing..." : "Review PR"}
                      </button>
                    )}
                    {(p.type === "pr_ci_failed" || p.type === "incident") && (
                      <button className="btn btn-sm btn-session"><Search size={10} /> Investigate</button>
                    )}
                    {p.type !== "suggestion" && (
                      <button className="btn btn-sm btn-create" onClick={async () => {
                        try {
                          await api.createTask({
                            id: `task-${p.id}`,
                            title: p.title,
                            type: "todo",
                            priority: p.priority,
                            source_pupdate_id: p.id,
                            url: p.url || "",
                            tags: p.tags || [],
                          });
                          showToast(`Task created: ${p.title.slice(0, 40)}...`, "normal");
                          handleMarkRead(p.id);
                        } catch (err) {
                          showToast("Couldn't create task", "high");
                        }
                      }}><CheckSquare size={10} /> Create Task</button>
                    )}
                    <button className="btn btn-sm btn-approve" onClick={async () => {
                      try {
                        await api.createProject({
                          id: `proj-${p.id}`,
                          title: p.title,
                          description: p.body || "",
                          priority: p.priority || "normal",
                        });
                        showToast(`Project created: ${p.title.slice(0, 40)}...`, "normal");
                        handleMarkRead(p.id);
                      } catch (err) {
                        showToast("Couldn't create project", "high");
                      }
                    }}><FolderKanban size={10} /> Create Project</button>
                    <button className="btn btn-sm btn-danger" onClick={(e) => handleDismiss(e, p.id)}>
                      <X size={10} /> Dismiss
                    </button>
                    {p.tags?.length > 0 && (
                      <div className="card-tags-inline">
                        {p.tags.map((t) => <span key={t} className="tag">{t}</span>)}
                      </div>
                    )}
                  </div>
                )}
              </div>
              <div className="card-right">
                <span className={`card-priority badge ${p.priority}`}>{p.priority}</span>
                <ChevronRight size={14} className={`card-chevron ${expanded === p.id ? "open" : ""}`} />
              </div>
            </div>
          ))}
        </div>
      )}
      </div>

      {/* Sidebar */}
      <div className="inbox-sidebar">
        <div className="home-widget">
          <div className="widget-header"><CheckSquare size={12} /> Focus</div>
          {tasks.length > 0 ? (
            <div className="sidebar-task-list">
              {tasks.slice(0, 4).map((t) => (
                <div key={t.id} className="sidebar-task">
                  <span className="sidebar-task-dot" style={{ background: "#60a5fa" }} />
                  <span className="sidebar-task-title">{t.title}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="widget-empty">No active tasks</div>
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
              <span className="sidebar-stat-val" style={{ color: "var(--blue)" }}>{tasks.length}</span>
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
            <span>Last: {brainStatus?.last_cycle ? new Date(brainStatus.last_cycle).toLocaleTimeString() : "Never"}</span>
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
              <div className="review-content" dangerouslySetInnerHTML={{ __html: renderReviewMarkdown(reviewResult.output) }} />
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

function renderReviewMarkdown(text) {
  if (!text) return "";
  return text
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/^\- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/^(?!<[hulo])(.+)$/gm, '<p>$1</p>')
    .replace(/<p><\/p>/g, '');
}
