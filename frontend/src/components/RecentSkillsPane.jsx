import { useEffect, useState } from "react";
import { Sparkles, ChevronDown, ChevronRight, X } from "lucide-react";
import { api } from "../api/client";
import { relativeTime } from "../utils/dates";
import { renderMarkdown } from "../utils/markdown";
import "./RecentSkillsPane.css";

/**
 * Sidebar widget listing the last few skill runs with expand-to-read
 * inline. Fed by kind=skill_result Memos — the skill-run endpoint
 * emits one per successful run (except for self-rendering skills
 * like home-overview that have their own surface).
 *
 * Zero-state stays visible so the widget doesn't pop in and out of
 * existence; once a user plays with a custom skill, the results
 * land here.
 */
const POLL_MS = 30_000;
const DEFAULT_LIMIT = 4;

export default function RecentSkillsPane() {
  const [memos, setMemos] = useState([]);
  const [expanded, setExpanded] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchMemos = async () => {
    try {
      // Default filter returns pending+seen — exactly what we want.
      const list = await api.getMemos({ kind: "skill_result", limit: 20 });
      setMemos(list || []);
    } catch {
      /* non-fatal */
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchMemos();
    const id = setInterval(fetchMemos, POLL_MS);
    const onFocus = () => fetchMemos();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const handleDismiss = async (id) => {
    try {
      await api.dismissMemo(id);
      setMemos((prev) => prev.filter((m) => m.id !== id));
      if (expanded === id) setExpanded(null);
    } catch {
      /* ignore */
    }
  };

  if (loading) return null;

  return (
    <div className="home-widget recent-skills-widget">
      <div className="widget-header">
        <Sparkles size={12} /> Recent skills
        {memos.length > 0 && (
          <span className="widget-count">{memos.length}</span>
        )}
      </div>
      {memos.length === 0 ? (
        <div className="widget-empty">
          No skill runs yet. Kick one off from Automations or a /skill endpoint.
        </div>
      ) : (
        <ul className="recent-skills-list">
          {memos.slice(0, DEFAULT_LIMIT).map((m) => {
            const isOpen = expanded === m.id;
            const skillName = m.extra?.skill_name || "skill";
            const full = m.body || "";
            const headline = (m.title || "").replace(new RegExp(`^${skillName}:\\s*`), "");
            return (
              <li key={m.id} className={`recent-skills-item${isOpen ? " expanded" : ""}`}>
                <button
                  type="button"
                  className="recent-skills-row"
                  onClick={() => setExpanded(isOpen ? null : m.id)}
                >
                  {isOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                  <span className="recent-skills-name">{skillName}</span>
                  <span className="recent-skills-headline">{headline || "(no preview)"}</span>
                  <span className="recent-skills-time">
                    {m.created_at ? relativeTime(m.created_at) : ""}
                  </span>
                </button>
                {isOpen && (
                  <div className="recent-skills-body">
                    <div
                      className="recent-skills-output markdown"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(full) }}
                    />
                    <div className="recent-skills-actions">
                      <button
                        type="button"
                        className="btn-ghost"
                        onClick={() => handleDismiss(m.id)}
                        title="Dismiss"
                      >
                        <X size={10} /> Dismiss
                      </button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
          {memos.length > DEFAULT_LIMIT && (
            <li className="recent-skills-more">
              + {memos.length - DEFAULT_LIMIT} more (see Inbox)
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
