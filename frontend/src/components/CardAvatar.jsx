import { useState } from "react";
import { useCards } from "../hooks/useCards";
import "./CardAvatar.css";

// Map size keywords → pixels. Numeric `size` is also accepted and
// passes straight through, so callers can use a one-off dimension.
const SIZE_PX = { xs: 16, sm: 24, md: 32, lg: 48, xl: 96 };

// Deterministic hue from the card id so the procedural fallback is
// stable across reloads. Same card → same color, even when the user
// hasn't dropped art for it yet.
function hueFromId(id) {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % 360;
}

/**
 * Avatar for an agent, resolved through their personality card.
 *
 * Resolves agent.avatar (= card_id) → card metadata → image at
 * /avatars/<id>.png. When the image is missing (404) or no card
 * is loaded yet, falls back to a procedural rounded square with
 * a per-card hue and the card's first initial.
 *
 * Props:
 *   agent     — { avatar, display_name } shape (from API)
 *   cardId    — alternative to `agent`, when only the id is known
 *   size      — keyword (xs|sm|md|lg|xl) or pixel number
 *   className — extra classes
 */
export default function CardAvatar({ agent, cardId, size = "md", className = "" }) {
  const id = cardId || agent?.avatar || "";
  const cards = useCards();
  const card = id ? cards.find((c) => c.id === id) : null;
  const px = typeof size === "number" ? size : SIZE_PX[size] || SIZE_PX.md;
  const [imgFailed, setImgFailed] = useState(false);

  // Used for alt text + aria-label only. No visible hover tooltip —
  // the bubble in PersistentPack covers the "who is this" question,
  // and the native browser tooltip was redundant noise on every avatar.
  const label = agent?.display_name || card?.display_name || id || "Agent";
  const initialSource = card?.display_name || agent?.display_name || id || "Agent";
  const initial = (initialSource.trim()[0] || "?").toUpperCase();
  const hue = hueFromId(id || label);

  if (id && !imgFailed) {
    return (
      <img
        src={`/avatars/${id}.png`}
        alt={label}
        className={`card-avatar card-avatar-${typeof size === "string" ? size : "n"} ${className}`}
        style={{ width: px, height: px }}
        onError={() => setImgFailed(true)}
      />
    );
  }

  return (
    <div
      className={`card-avatar card-avatar-fallback card-avatar-${typeof size === "string" ? size : "n"} ${className}`}
      style={{
        width: px,
        height: px,
        background: `hsl(${hue}, 55%, 72%)`,
        color: `hsl(${hue}, 75%, 22%)`,
        fontSize: Math.max(10, Math.round(px * 0.5)),
      }}
      aria-label={label}
    >
      {initial}
    </div>
  );
}
