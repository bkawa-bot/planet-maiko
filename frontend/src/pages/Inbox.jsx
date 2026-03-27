import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ExternalLink, X, Eye, Search, Inbox as InboxIcon } from "lucide-react";
import "./Inbox.css";

const TABS = [
  { id: "all", label: "All", filter: () => true },
  { id: "prs", label: "PRs", filter: (p) => p.type?.startsWith("pr_") },
  { id: "calendar", label: "Calendar", filter: (p) => p.source === "calendar" },
  { id: "system", label: "System", filter: (p) => p.source === "maiko" || p.source === "agent" },
];

const PRIORITY_ORDER = { urgent: 0, high: 1, normal: 2, low: 3 };

export default function Inbox() {
  const [pupdates, setPupdates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("all");
  const [expanded, setExpanded] = useState(null);

  const fetchPupdates = async () => {
    setLoading(true);
    try {
      const data = await api.getPupdates();
      data.sort((a, b) => (PRIORITY_ORDER[a.priority] ?? 99) - (PRIORITY_ORDER[b.priority] ?? 99));
      setPupdates(data);
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
        {filtered.length > 0 && (
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
          <InboxIcon size={36} className="empty-icon" />
          <div className="empty-title">Nothing here</div>
          <div className="empty-sub">All clear in this category!</div>
        </div>
      ) : (
        <div className="card-list">
          {filtered.map((p) => (
            <div
              key={p.id}
              className={`card pupdate-card ${p.priority} ${p.read ? "read" : ""} ${expanded === p.id ? "expanded" : ""}`}
              onClick={() => toggleExpand(p)}
            >
              <div className="card-left-bar" />
              <div className="card-content">
                <div className="card-top">
                  <span className="card-source">{p.source}</span>
                  <span className="card-title">
                    {p.url ? <a href={p.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{p.title}</a> : p.title}
                  </span>
                  <span className={`badge ${p.priority}`}>{p.priority}</span>
                </div>
                <div className="card-meta">
                  <span className="card-type">{p.type?.replace(/_/g, " ")}</span>
                  <span className="card-time">{new Date(p.timestamp).toLocaleString()}</span>
                  {p.actionable && <span className="card-action-hint">{p.action_hint}</span>}
                  {p.tags?.map((t) => <span key={t} className="tag">{t}</span>)}
                </div>

                {expanded === p.id && (
                  <div className="card-body">
                    {p.body && <div className="card-body-text">{p.body}</div>}
                  </div>
                )}
              </div>
              <div className="card-actions">
                {p.url && (
                  <a href={p.url} target="_blank" rel="noreferrer" className="btn btn-sm" onClick={(e) => e.stopPropagation()}>
                    <ExternalLink size={10} /> Open
                  </a>
                )}
                {p.type === "pr_review_requested" && (
                  <button className="btn btn-sm" onClick={(e) => e.stopPropagation()}>
                    <Eye size={10} /> Review
                  </button>
                )}
                {(p.type === "pr_ci_failed" || p.type === "incident") && (
                  <button className="btn btn-sm" onClick={(e) => e.stopPropagation()}>
                    <Search size={10} /> Investigate
                  </button>
                )}
                <button className="btn btn-sm btn-danger" onClick={(e) => handleDismiss(e, p.id)}>
                  <X size={10} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
