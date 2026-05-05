import { useState } from "react";
import { useCards } from "../hooks/useCards";
import "./CardArt.css";

// Soft baseline gradients keyed off rarity — used by the procedural
// fallback so each tier has a distinct vibe even before art lands.
// Real card images replace these entirely; values are HSL hues.
const RARITY_HUE = {
  common: 210,      // muted blue
  uncommon: 140,    // sage green
  rare: 260,        // soft purple
  epic: 320,        // dusty pink
  legendary: 38,    // warm amber
};

function hueFromId(id) {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) | 0;
  }
  return Math.abs(hash) % 360;
}

/**
 * Full baseball-card art for an agent's archetype.
 *
 * Tries `/cards/<id>.png`; on 404 (or before art exists), renders a
 * procedural gradient with the archetype name and tagline. Rarity
 * still drives the fallback's base hue (so legendary archetypes
 * read as gold-ish and commons cooler) but isn't surfaced as a label.
 */
export default function CardArt({ cardId, agent, className = "" }) {
  const id = cardId || agent?.avatar || "";
  const cards = useCards();
  const card = id ? cards.find((c) => c.id === id) : null;
  const [imgFailed, setImgFailed] = useState(false);

  const rarity = card?.rarity || "common";
  const baseHue = RARITY_HUE[rarity] ?? hueFromId(id || "default");
  const accentHue = (baseHue + 30) % 360;

  return (
    <div className={`card-art ${className}`}>
      {id && !imgFailed ? (
        <img
          src={`/cards/${id}.png`}
          alt={card?.display_name || id}
          className="card-art-image"
          onError={() => setImgFailed(true)}
        />
      ) : (
        <div
          className="card-art-fallback"
          style={{
            background: `linear-gradient(135deg, hsl(${baseHue}, 55%, 78%), hsl(${accentHue}, 55%, 60%))`,
            color: `hsl(${baseHue}, 70%, 18%)`,
          }}
        >
          <div className="card-art-fallback-name">
            {card?.display_name || id || "Mystery"}
          </div>
          {card?.tagline && (
            <div className="card-art-fallback-tagline">{card.tagline}</div>
          )}
        </div>
      )}
    </div>
  );
}
