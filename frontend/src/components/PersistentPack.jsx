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
  // throttles pointer updates to one per frame, the latest cursor Y so
  // the scheduled frame always uses the freshest position, and the
  // avatars' *resting* centers/sizes. We cache the rest positions because
  // getBoundingClientRect reflects the live transforms we apply — true
  // rest can only be read while no transform is set.
  const packRef = useRef(null);
  const rafRef = useRef(0);
  const lastYRef = useRef(0);
  const baseRef = useRef([]);

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

  // Snapshot each avatar's resting center + size. Clears any live
  // transform first so the rects we read are the true rest layout (this
  // is the one place we force a synchronous reflow — only on a gesture's
  // first frame or when the pack's contents change, never per frame).
  const measureBase = (wraps) => {
    wraps.forEach((el) => { el.style.transform = ""; });
    baseRef.current = wraps.map((el) => {
      const r = el.getBoundingClientRect();
      return { center: r.top + r.height / 2, size: r.height };
    });
  };

  // macOS-dock magnification. Each avatar scales by how close the cursor
  // is to its resting center, AND shifts away from the cursor by the
  // combined growth of the avatars between it and the cursor — so the
  // big one under the pointer pushes its neighbors outward instead of
  // overlapping them. The avatar at the cursor stays anchored; the column
  // grows outward from it. Written straight to the DOM (no React
  // re-render) so it tracks the pointer at 60fps. We transform the avatar
  // wrap, not the button, so the hover bubble keeps its size.
  const magnify = (clientY) => {
    const root = packRef.current;
    if (!root) return;
    const wraps = [...root.querySelectorAll(".persistent-pack-avatar-wrap")];
    if (!wraps.length) return;
    // Re-measure when the cache is empty or the pack's size changed
    // (an agent joined/left); otherwise reuse the cached rest layout.
    if (baseRef.current.length !== wraps.length) measureBase(wraps);
    const base = baseRef.current;
    const n = wraps.length;

    const scale = new Array(n);
    const grow = new Array(n); // px added to this avatar's height
    for (let i = 0; i < n; i++) {
      const t = Math.max(0, 1 - Math.abs(base[i].center - clientY) / MAG_RADIUS);
      const eased = t * t * (3 - 2 * t); // smoothstep falloff
      scale[i] = 1 + MAG_STRENGTH * eased;
      grow[i] = (scale[i] - 1) * base[i].size;
    }

    for (let i = 0; i < n; i++) {
      const d = base[i].center - clientY;
      const dir = d > 0 ? 1 : d < 0 ? -1 : 0;
      // Half of this avatar's own growth keeps its near edge put, plus
      // the full growth of every avatar sitting between it and the cursor.
      let push = grow[i] / 2;
      for (let j = 0; j < n; j++) {
        if (j === i) continue;
        const dj = base[j].center - clientY;
        if (dir > 0 ? dj > 0 && dj < d : dir < 0 ? dj < 0 && dj > d : false) {
          push += grow[j];
        }
      }
      wraps[i].style.transform = `translateY(${(dir * push).toFixed(2)}px) scale(${scale[i].toFixed(4)})`;
    }
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
