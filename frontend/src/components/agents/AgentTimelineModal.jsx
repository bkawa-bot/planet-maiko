import { useEffect, useState } from "react";
import { X, Clock } from "lucide-react";
import { api } from "../../api/client";
import { relativeTime } from "../../utils/dates";

/**
 * Per-agent chronological activity view. Every pupdate involving this
 * agent_id across all tasks, newest first.
 *
 * Replaces the cross-cutting "recent activity" role the Inbox tab
 * used to play, scoped to one agent's thread of work. Non-actionable
 * pupdates (agent pushed to GitHub, committed, etc.) live here — they
 * stay out of the Home action feed but are inspectable on demand for
 * anyone who wants the full log for a single agent.
 *
 * Props:
 *   agentId    — string, the profile id (e.g. "agent-coding-...")
 *   agentName  — string, display name for the modal title
 *   onClose    — () => void
 */
export default function AgentTimelineModal({ agentId, agentName, onClose }) {
  const [pupdates, setPupdates] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        // Backend doesn't expose an /agents/:id/timeline endpoint yet —
        // fetch the full pupdate list and filter frontend-side. The
        // attribution fields are inconsistent across pupdate types
        // (metadata.agent_id, extra.agent_id, tags, source_id), so
        // check all of them.
        const all = await api.getPupdates();
        const mine = all.filter((p) => {
          if (p.metadata?.agent_id === agentId) return true;
          if (p.extra?.agent_id === agentId) return true;
          if (Array.isArray(p.tags) && p.tags.includes(agentId)) return true;
          if (p.source_id === `agent/${agentId}`) return true;
          return false;
        });
        mine.sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
        if (!cancelled) setPupdates(mine);
      } catch (err) {
        // Network / backend hiccup — leave the list empty and surface
        // nothing to the user; the modal just shows the empty state.
        if (!cancelled) setPupdates([]);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [agentId]);

  if (!agentId) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="thread-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <Clock size={14} /> {agentName || agentId}'s timeline
          <span style={{ flex: 1 }} />
          <button className="btn btn-sm modal-close-btn" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        <div className="thread-messages">
          {loading ? (
            <p className="page-empty thread-empty">Looking…</p>
          ) : pupdates.length === 0 ? (
            <p className="page-empty thread-empty">
              Nothing yet — {agentName || "this agent"} hasn't surfaced any activity.
            </p>
          ) : (
            pupdates.map((p) => {
              const taskId = p.metadata?.task_id || p.extra?.task_id;
              return (
                <div key={p.id} className="thread-msg">
                  <div className="thread-msg-header">
                    <span className="thread-msg-sender">
                      {(p.type || "pupdate").replace(/_/g, " ")}
                    </span>
                    {taskId && <span className="badge">{taskId}</span>}
                    <span className="thread-msg-time">{relativeTime(p.timestamp)}</span>
                  </div>
                  <div className="thread-msg-content">
                    <div>{p.title}</div>
                    {p.body && (
                      <div style={{ marginTop: 4, opacity: 0.75, fontSize: 12 }}>
                        {p.body}
                      </div>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
