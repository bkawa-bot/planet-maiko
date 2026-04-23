import { useEffect, useState } from "react";
import { Bell, X, ExternalLink } from "lucide-react";
import { api } from "../api/client";
import { relativeTime } from "../utils/dates";
import { renderMarkdown } from "../utils/markdown";
import "./NotificationsPane.css";

/**
 * Home pane for kind=notification Memos — the surface for the
 * notify_me automation action. Auto-hides when the list is empty so
 * quiet days don't grow an empty bucket. Items are dismissable, and
 * clicking a notification with a url opens that url; otherwise it's
 * just informational.
 *
 * Positioned between OverviewPane and ReviewQueue so it reads as an
 * "oh, something you asked to know about just happened" beat without
 * competing with the primary greeting.
 */
const POLL_MS = 30_000;
const DEFAULT_LIMIT = 5;

const PRIORITY_TONE = {
  urgent: "urgent",
  high: "high",
  normal: "normal",
  low: "low",
};

export default function NotificationsPane() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = async () => {
    try {
      const list = await api.getMemos({ kind: "notification", limit: 50 });
      setItems(list || []);
    } catch {
      /* non-fatal — pane stays with its last-known state */
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, POLL_MS);
    const onFocus = () => fetchAll();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const dismiss = async (id) => {
    setItems((prev) => prev.filter((m) => m.id !== id));
    try {
      await api.dismissMemo(id);
    } catch {
      // If the dismiss call failed, next poll will restore the row —
      // user will see it again and can retry.
    }
  };

  if (loading) return null;
  if (items.length === 0) return null;

  const visible = items.slice(0, DEFAULT_LIMIT);

  return (
    <div className="notifications-pane frost-pane">
      <div className="notifications-header">
        <Bell size={12} /> Notifications
        <span className="notifications-count">{items.length}</span>
      </div>
      <ul className="notifications-list">
        {visible.map((m) => {
          const tone = PRIORITY_TONE[m.priority] || "normal";
          return (
            <li
              key={m.id}
              className={`notifications-item tone-${tone}`}
            >
              <div className="notifications-body">
                <div className="notifications-title">{m.title || "(notification)"}</div>
                {m.body && (
                  <div
                    className="notifications-detail markdown"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(m.body) }}
                  />
                )}
                <div className="notifications-meta">
                  {m.priority && m.priority !== "normal" && (
                    <span className={`notifications-priority notifications-priority-${tone}`}>
                      {m.priority}
                    </span>
                  )}
                  {m.created_at && (
                    <span className="notifications-time">{relativeTime(m.created_at)}</span>
                  )}
                </div>
              </div>
              <div className="notifications-actions">
                {m.url && (
                  <a
                    href={m.url}
                    target="_blank"
                    rel="noreferrer"
                    className="btn btn-sm"
                    title="Open source"
                  >
                    <ExternalLink size={10} />
                  </a>
                )}
                <button
                  className="btn-ghost notifications-dismiss"
                  onClick={() => dismiss(m.id)}
                  title="Dismiss"
                  aria-label="Dismiss"
                >
                  <X size={12} />
                </button>
              </div>
            </li>
          );
        })}
        {items.length > DEFAULT_LIMIT && (
          <li className="notifications-more">
            + {items.length - DEFAULT_LIMIT} more (check Inbox)
          </li>
        )}
      </ul>
    </div>
  );
}
