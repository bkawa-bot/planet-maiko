import { useEffect, useRef, useState } from "react";
import { Code2, Eye, Search, Map } from "lucide-react";
import { api } from "../api/client";
import CardArt from "./CardArt";
import RarityBadge from "./RarityBadge";
import ModalPortal from "./ModalPortal";
import { useCards } from "../hooks/useCards";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import "./ArrivalWatcher.css";

const SEEN_KEY = "maiko-seen-arrivals";
const POLL_MS = 15_000;

const ROLE_META = {
  coding: { icon: Code2, label: "Coder" },
  review: { icon: Eye, label: "Reviewer" },
  investigation: { icon: Search, label: "Investigator" },
  cartographer: { icon: Map, label: "Cartographer" },
};

function loadSeen() {
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
}

function saveSeen(set) {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify([...set]));
  } catch { /* private mode / quota — best-effort */ }
}

/**
 * Polls for agents whose arrival bio-gen has resolved and pops a
 * celebratory full-card modal once per agent. Mounted at App root
 * so it fires regardless of route. Modal persists until dismissed —
 * the arrival itself IS the announcement, no auto-close.
 *
 * Dedup lives in localStorage so a refresh doesn't re-show modals
 * the user already welcomed; the backend's 30-min window puts a
 * natural cap on how long a never-acknowledged agent stays eligible.
 */
export default function ArrivalWatcher() {
  const defaultOrg = useDefaultOrg();
  const cards = useCards();
  const [queue, setQueue] = useState([]);
  // seen lives in a ref so the polling closure reads the latest set
  // without the effect having to re-subscribe on every dismissal.
  const seenRef = useRef(loadSeen());

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const tick = async () => {
      try {
        const rows = await api.getJustArrivedProfiles();
        if (cancelled) return;
        const fresh = (rows || []).filter((p) => !seenRef.current.has(p.id));
        if (fresh.length > 0) {
          setQueue((prev) => {
            const inQueue = new Set(prev.map((p) => p.id));
            const additions = fresh.filter((p) => !inQueue.has(p.id));
            return additions.length ? [...prev, ...additions] : prev;
          });
        }
      } catch { /* offline / server restart — silent retry next tick */ }
      if (!cancelled) timer = setTimeout(tick, POLL_MS);
    };

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  if (queue.length === 0) return null;

  const profile = queue[0];
  const card = cards.find((c) => c.id === profile.avatar);
  const role = profile.role || "coding";
  const meta = ROLE_META[role] || ROLE_META.coding;
  const RoleIcon = meta.icon;

  const dismiss = () => {
    const next = new Set(seenRef.current);
    next.add(profile.id);
    seenRef.current = next;
    saveSeen(next);
    setQueue((prev) => prev.slice(1));
  };

  return (
    <ModalPortal>
      <div className="modal-overlay arrival-watcher-overlay" onClick={dismiss}>
        <div className="arrival-watcher-modal" onClick={(e) => e.stopPropagation()}>
          <div className="arrival-watcher-eyebrow">A new agent arrived</div>
          <div className="arrival-watcher-split">
            {/* Left column: hero card art + archetype tagline below it,
                mirroring the profile detail modal so the two surfaces
                feel like the same family. */}
            <div className="arrival-watcher-left">
              <CardArt cardId={profile.avatar} className="arrival-watcher-art" />
              {card?.tagline && (
                <div className="arrival-watcher-archetype-tagline">
                  {card.tagline}
                </div>
              )}
            </div>
            {/* Right column: identity, role, the agent's own voice
                (flavor_text tagline + bio), confirm. The bio is the
                agent's self-written intro -- usually 2-3 sentences
                that establish character. */}
            <div className="arrival-watcher-right">
              {card?.rarity && (
                <RarityBadge rarity={card.rarity} size="lg" />
              )}
              <div className="arrival-watcher-name">{profile.display_name}</div>
              {card && (
                <div className="arrival-watcher-archetype">
                  <span className="arrival-watcher-archetype-label">Type:</span>
                  {card.display_name}
                </div>
              )}
              <div className="arrival-watcher-role">
                <RoleIcon size={11} /> {meta.label}
                <span className="arrival-watcher-scope">
                  {" · "}
                  {profile.scope_repo ? formatRepo(profile.scope_repo, defaultOrg) : "global"}
                </span>
              </div>
              {profile.flavor_text && (
                <div className="arrival-watcher-flavor">"{profile.flavor_text}"</div>
              )}
              {profile.instructions && (
                <div className="arrival-watcher-bio">{profile.instructions}</div>
              )}
              <button className="btn btn-primary arrival-watcher-confirm" onClick={dismiss}>
                Welcome {profile.display_name}
              </button>
            </div>
          </div>
        </div>
      </div>
    </ModalPortal>
  );
}
