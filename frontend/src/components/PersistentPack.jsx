import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import CardAvatar from "./CardAvatar";
import "./PersistentPack.css";

const POLL_MS = 15_000;
const VISITS_KEY = "maiko-pack-visits";

/**
 * Persistent pack dock — the active-agents avatar stack pinned to the
 * bottom-left of every page. Click an avatar to jump to that agent's
 * job chat. An unread dot pulses on agents whose `status` is "waiting"
 * or "ready" AND whose latest activity timestamp postdates the user's
 * last click-through (the click-as-visit signal lives in localStorage).
 *
 * Hidden on /jobs/<id> because you're already in the chat context
 * there; redundant chrome.
 *
 * Hidden when the pack is empty — no agents running, no widget.
 */
function loadVisits() {
  try {
    return JSON.parse(localStorage.getItem(VISITS_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveVisits(map) {
  try {
    localStorage.setItem(VISITS_KEY, JSON.stringify(map));
  } catch { /* quota / private mode — best effort */ }
}

export default function PersistentPack() {
  const navigate = useNavigate();
  const location = useLocation();
  const [activity, setActivity] = useState([]);
  const [expanded, setExpanded] = useState(false);
  const visitsRef = useRef(loadVisits());

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const tick = async () => {
      try {
        const rows = await api.getAgentActivity();
        if (!cancelled) setActivity(Array.isArray(rows) ? rows : []);
      } catch { /* offline / restart — silent retry next tick */ }
      if (!cancelled) timer = setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // /jobs/<id> already shows the chat — extra dock chrome there is
  // noise, not signal.
  if (location.pathname.startsWith("/jobs/")) return null;

  if (activity.length === 0) return null;

  const handleAvatarClick = (a) => {
    const id = a.job_id || a.task_id;
    const next = { ...visitsRef.current, [id]: Date.now() };
    visitsRef.current = next;
    saveVisits(next);
    navigate(`/jobs/${id}?view=chat`);
  };

  const hasUnread = (a) => {
    const status = a.status || "active";
    // Only "waiting" (agent asked for input) and "ready"
    // (FOLLOWUP_KINDS job done, available for follow-up) earn a badge.
    // Plain "active" agents aren't blocked on the user.
    if (status !== "waiting" && status !== "ready") return false;
    const id = a.job_id || a.task_id;
    const lastVisit = visitsRef.current[id] || 0;
    const lastSeen = a.last_seen ? new Date(a.last_seen).getTime() : 0;
    return lastSeen > lastVisit;
  };

  return (
    <div
      className={`persistent-pack ${expanded ? "expanded" : "collapsed"}`}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
    >
      {activity.map((a) => {
        const id = a.job_id || a.task_id;
        const unread = hasUnread(a);
        const name = a.agent_name || "agent";
        const status = a.status || "active";
        const agentForAvatar = {
          avatar: a.agent_avatar,
          display_name: name,
        };
        return (
          <button
            key={id}
            type="button"
            className={`persistent-pack-villager pack-status-${status}`}
            onClick={() => handleAvatarClick(a)}
            onFocus={() => setExpanded(true)}
            onBlur={() => setExpanded(false)}
            title={`${name} — ${a.task_title || a.task_type || "working"}`}
          >
            <span className="persistent-pack-avatar-wrap">
              <CardAvatar
                agent={agentForAvatar}
                size={expanded ? 40 : 32}
              />
              <span
                className={`persistent-pack-state-dot state-${status}`}
                aria-label={`status: ${status}`}
              />
              {unread && (
                <span
                  className="persistent-pack-unread-dot"
                  aria-label="new message"
                />
              )}
            </span>
            {expanded && (
              <span className="persistent-pack-bubble" role="tooltip">
                <span className="persistent-pack-bubble-name">{name}</span>
                {a.task_title && (
                  <span className="persistent-pack-bubble-title">{a.task_title}</span>
                )}
                {a.last_message && (
                  <span className="persistent-pack-bubble-msg">
                    "{a.last_message}"
                  </span>
                )}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
