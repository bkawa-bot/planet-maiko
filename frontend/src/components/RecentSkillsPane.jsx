import { useEffect, useState } from "react";
import { Sparkles, ChevronDown, ChevronRight, X } from "lucide-react";
import { api } from "../api/client";
import { relativeTime } from "../utils/dates";
import { renderMarkdown } from "../utils/markdown";
import "./RecentSkillsPane.css";

/**
 * Sidebar widget listing the last few skill runs with expand-to-read
 * inline. Fed by skill_result pupdates — the run endpoint emits one
 * per successful run (except for self-rendering skills like
 * home-overview that have their own surface).
 *
 * Zero-state stays visible so the widget doesn't pop in and out of
 * existence; once a user plays with a custom skill, the results
 * land here.
 */
const POLL_MS = 30_000;
const DEFAULT_LIMIT = 4;

export default function RecentSkillsPane() {
  const [pupdates, setPupdates] = useState([]);
  const [expanded, setExpanded] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetch = async () => {
    try {
      // Server returns non-dismissed by default + newest-first.
      const list = await api.getPupdates({ limit: 20 });
      const skills = (list || []).filter((p) => p.type === "skill_result");
      setPupdates(skills);
    } catch {
      /* non-fatal */
    }
    setLoading(false);
  };

  useEffect(() => {
    fetch();
    const id = setInterval(fetch, POLL_MS);
    const onFocus = () => fetch();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const handleDismiss = async (id) => {
    try {
      await api.dismissPupdate(id);
      setPupdates((prev) => prev.filter((p) => p.id !== id));
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
        {pupdates.length > 0 && (
          <span className="widget-count">{pupdates.length}</span>
        )}
      </div>
      {pupdates.length === 0 ? (
        <div className="widget-empty">
          No skill runs yet. Kick one off from Automations or a /skill endpoint.
        </div>
      ) : (
        <ul className="recent-skills-list">
          {pupdates.slice(0, DEFAULT_LIMIT).map((p) => {
            const isOpen = expanded === p.id;
            const skillName = p.metadata?.skill_name || p.extra?.skill_name || "skill";
            const full = p.metadata?.full_output || p.extra?.full_output || p.body || "";
            const headline = (p.title || "").replace(new RegExp(`^${skillName}:\\s*`), "");
            return (
              <li key={p.id} className={`recent-skills-item${isOpen ? " expanded" : ""}`}>
                <button
                  type="button"
                  className="recent-skills-row"
                  onClick={() => setExpanded(isOpen ? null : p.id)}
                >
                  {isOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                  <span className="recent-skills-name">{skillName}</span>
                  <span className="recent-skills-headline">{headline || "(no preview)"}</span>
                  <span className="recent-skills-time">
                    {p.timestamp ? relativeTime(p.timestamp) : ""}
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
                        onClick={() => handleDismiss(p.id)}
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
          {pupdates.length > DEFAULT_LIMIT && (
            <li className="recent-skills-more">
              + {pupdates.length - DEFAULT_LIMIT} more (see Inbox)
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
