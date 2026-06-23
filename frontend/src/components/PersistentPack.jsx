import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Plus } from "@icons";
import CardAvatar from "./CardAvatar";
import "./PersistentPack.css";

const POLL_MS = 15_000;
const VISITS_KEY = "maiko-pack-visits";
// Dock magnification: how far (px) along the column the cursor's pull
// reaches, and the peak extra scale on the avatar directly under it
// (0.45 → 1.45×). Falloff is smoothstepped so neighbors swell gently
// the way the macOS dock does, instead of one avatar popping alone.
const MAG_RADIUS = 90;
const MAG_STRENGTH = 0.45;

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
  const [activity, setActivity] = useState([]);
  const visitsRef = useRef(loadVisits());
  // Dock magnification refs: the column element, the rAF handle that
  // throttles pointer updates to one per frame, and the latest cursor Y
  // so the scheduled frame always uses the freshest position.
  const packRef = useRef(null);
  const rafRef = useRef(0);
  const lastYRef = useRef(0);

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

  // Drop any pending magnification frame if we unmount mid-gesture.
  useEffect(() => () => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  }, []);

  // Scale every avatar by how close the cursor is to its center —
  // applied straight to the DOM (no React re-render) so it tracks the
  // pointer at 60fps. We scale the avatar wrap, not the whole button,
  // so the hover speech-bubble stays its normal size.
  const magnify = (clientY) => {
    const root = packRef.current;
    if (!root) return;
    root.querySelectorAll(".persistent-pack-avatar-wrap").forEach((el) => {
      const r = el.getBoundingClientRect();
      const center = r.top + r.height / 2;
      const t = Math.max(0, 1 - Math.abs(clientY - center) / MAG_RADIUS);
      const eased = t * t * (3 - 2 * t); // smoothstep
      el.style.transform = `scale(${1 + MAG_STRENGTH * eased})`;
    });
  };

  const handleMove = (e) => {
    lastYRef.current = e.clientY;
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      magnify(lastYRef.current);
    });
  };

  const handleLeave = () => {
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = 0; }
    const root = packRef.current;
    if (!root) return;
    // Hand the avatars back to their resting size; the CSS transition
    // eases the settle.
    root.querySelectorAll(".persistent-pack-avatar-wrap").forEach((el) => {
      el.style.transform = "";
    });
  };

  // The dock stays visible on /jobs/<id> too -- the user wants to
  // hop between agent conversations without going back home, which
  // is the whole point of making it persistent.
  //
  // Maiko's slot is always rendered first regardless of pack
  // activity, so the dock never disappears entirely.

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
      className="persistent-pack"
      ref={packRef}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
    >
      <button
        type="button"
        className="persistent-pack-villager persistent-pack-maiko"
        onClick={() => navigate("/maiko")}
        aria-label="Maiko, the controller"
      >
        <span className="persistent-pack-avatar-wrap persistent-pack-maiko-wrap">
          {/* Drops a real Maiko sprite (photo or pixel-art portrait) at
              /public/sprites/maiko-avatar.png and it replaces the planet
              icon here. Until then onError falls back to the planet. */}
          <img
            src="/sprites/maiko-avatar.png"
            onError={(e) => { e.currentTarget.src = "/icon.svg"; }}
            alt=""
            width={44}
            height={44}
            className="persistent-pack-maiko-avatar"
          />
        </span>
        <span className="persistent-pack-bubble" role="tooltip">
          <span className="persistent-pack-bubble-name">Maiko</span>
          <span className="persistent-pack-bubble-title">she sees the pack</span>
        </span>
      </button>
      {activity.length > 0 && (
        <span className="persistent-pack-divider" aria-hidden="true" />
      )}
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
            aria-label={`${name} — ${a.task_title || a.task_type || "working"}`}
          >
            <span className="persistent-pack-avatar-wrap">
              <CardAvatar
                agent={agentForAvatar}
                size={44}
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
          </button>
        );
      })}
      {activity.length > 0 && (
        <span className="persistent-pack-divider" aria-hidden="true" />
      )}
      <button
        type="button"
        className="persistent-pack-villager persistent-pack-add"
        onClick={() => window.dispatchEvent(new CustomEvent("open-launch-agent"))}
        aria-label="Launch a new agent"
      >
        <span className="persistent-pack-avatar-wrap persistent-pack-add-wrap">
          <Plus size={18} />
        </span>
        <span className="persistent-pack-bubble" role="tooltip">
          <span className="persistent-pack-bubble-name">Launch an agent</span>
          <span className="persistent-pack-bubble-title">Pick agent + prompt. Cmd/Ctrl+K too.</span>
        </span>
      </button>
    </div>
  );
}
