import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ExternalLink, X, Eye, Search, Inbox as InboxIcon, ClipboardCheck, FolderKanban, CheckSquare, MoreHorizontal, MessageCircle, Pencil, GitBranch, Calendar as CalendarIcon, Bot, Lightbulb, AlertTriangle, MessageSquare, ChevronRight } from "lucide-react";
import "./Inbox.css";

const TABS = [
  { id: "all", label: "All", filter: (p) => p.type !== "approval" },
  { id: "prs", label: "PRs", filter: (p) => p.type?.startsWith("pr_") },
  { id: "calendar", label: "Calendar", filter: (p) => p.source === "calendar" },
  { id: "approvals", label: "Approvals", filter: (p) => p.type === "approval" || p.metadata?.needs_approval },
  { id: "system", label: "System", filter: (p) => p.source === "maiko" || p.source === "agent" },
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
  const [brainStatus, setBrainStatus] = useState(null);
  const [tasks, setTasks] = useState([]);

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
            {tabCounts[t.id] > 0 && <span className="tab-badge">{tabCounts[t.id]}</span>}
          </button>
        ))}
        {filtered.length > 0 && tab !== "approvals" && (
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

      {loading ? (
        <p className="page-empty">Loading...</p>
      ) : filtered.length === 0 ? (
        <div className="empty-state">
          {tab === "approvals" ? (
            <>
              <ClipboardCheck size={36} className="empty-icon" />
              <div className="empty-title">Nothing waiting for approval</div>
              <div className="empty-sub">Project plans, suggested plans, and drafts will appear here</div>
            </>
          ) : (
            <>
              <InboxIcon size={36} className="empty-icon" />
              <div className="empty-title">Nothing here</div>
              <div className="empty-sub">All clear in this category!</div>
            </>
          )}
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
                  {p.actionable && <span className="card-action-hint">{p.action_hint}</span>}
                </div>

                {expanded === p.id && p.body && (
                  <div className="rich-body">{p.body}</div>
                )}

                {/* Inline actions — show when expanded */}
                {expanded === p.id && (
                  <div className="card-inline-actions" onClick={(e) => e.stopPropagation()}>
                    {p.url && (
                      <a href={p.url} target="_blank" rel="noreferrer" className="btn btn-sm">
                        <ExternalLink size={10} /> Open
                      </a>
                    )}
                    {p.type === "pr_review_requested" && (
                      <button className="btn btn-sm btn-action"><Eye size={10} /> Review PR</button>
                    )}
                    {(p.type === "pr_ci_failed" || p.type === "incident") && (
                      <button className="btn btn-sm btn-session"><Search size={10} /> Investigate</button>
                    )}
                    {(p.type === "approval" || p.metadata?.needs_approval) && (
                      <button className="btn btn-sm btn-approve"><FolderKanban size={10} /> Create Project</button>
                    )}
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
    </div>
  );
}
