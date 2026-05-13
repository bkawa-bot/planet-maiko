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
    // Snapshot the agent's current state at click time. The badge is
    // a function of "has anything I haven't seen happened since I
    // last opened this chat?", and we judge that by comparing the
    // current state to this snapshot. Tracking last_message (the
    // actual message body) is more accurate than a wall-clock
    // timestamp, because last_seen ticks on every poll and would
    // make any waiting/ready agent permanently unread.
    const next = {
      ...visitsRef.current,
      [id]: {
        message: a.last_message || "",
        status: a.status || "active",
      },
    };
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
    const snapshot = visitsRef.current[id];
    // Never visited → badge shows.
    if (!snapshot || typeof snapshot !== "object") return true;
    // Visited, but the agent has spoken again since (last_message
    // text differs from what we saw) → badge re-appears.
    if ((a.last_message || "") !== (snapshot.message || "")) return true;
    return false;
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
                size={expanded ? 56 : 44}
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
