/**
 * Rarity pill for a card archetype. Renders nothing if rarity is missing.
 *
 * Hues match the RARITY_HUE table in CardArt.jsx so the badge and the
 * card-art fallback gradient stay in the same color family per tier.
 * Theme-agnostic: rarity colors are absolute (a legendary is gold
 * everywhere), not theme-relative, so inline HSL is the right tool here.
 */
const RARITY_META = {
  common:    { hue: 210, label: "Common" },
  uncommon:  { hue: 140, label: "Uncommon" },
  rare:      { hue: 260, label: "Rare" },
  epic:      { hue: 320, label: "Epic" },
  legendary: { hue: 38,  label: "Legendary" },
};

export default function RarityBadge({ rarity, size = "sm" }) {
  if (!rarity) return null;
  const meta = RARITY_META[rarity] || RARITY_META.common;
  const fontSize = size === "lg" ? 11 : 10;
  return (
    <span
      className={`rarity-badge rarity-${rarity}`}
      style={{
        background: `hsl(${meta.hue}, 65%, 94%)`,
        color: `hsl(${meta.hue}, 75%, 28%)`,
        border: `1px solid hsl(${meta.hue}, 55%, 78%)`,
        padding: "2px 8px",
        borderRadius: 10,
        fontSize,
        fontWeight: 600,
        letterSpacing: "0.6px",
        textTransform: "uppercase",
        display: "inline-block",
        lineHeight: 1.4,
      }}
    >
      {meta.label}
    </span>
  );
}
