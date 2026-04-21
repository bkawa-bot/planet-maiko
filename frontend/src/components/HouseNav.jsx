import { NavLink } from "react-router-dom";
import "./HouseNav.css";

/**
 * Prototype nav — cross-section cottage with lit windows.
 *
 * The cottage is painted via a static SVG (roof, walls, chimney,
 * grass); the five nav windows are absolutely-positioned NavLinks on
 * top. Each window is a soft-glow rounded rect with an emoji icon.
 * Active window gets a warmer fill and a drifting "smoke from a
 * lamplit room" feel via a brighter glow.
 *
 * Designed to live as a floating panel in the lower-left corner of
 * the viewport so it feels like your cottage on the hillside, not a
 * nav chrome bar.
 */

const WINDOWS = [
  // Coords expressed in percent of the container so the SVG and the
  // overlay stay aligned at any size. Top-left origin.
  { to: "/",            end: true, label: "Home",        emoji: "🏡", x: 48, y: 70, size: "big"   },
  { to: "/tasks",                  label: "Tasks",       emoji: "📋", x: 25, y: 45, size: "small" },
  { to: "/knowledge",              label: "Knowledge",   emoji: "🧠", x: 48, y: 45, size: "small" },
  { to: "/automations",            label: "Automations", emoji: "⚡", x: 71, y: 45, size: "small" },
  { to: "/agents",                 label: "Pack",        emoji: "🐾", x: 75, y: 70, size: "big"   },
];


export default function HouseNav() {
  return (
    <div className="house-nav" role="navigation" aria-label="Primary">
      {/* A little drifting smoke plume — CSS animates it rising from
          the chimney, pure vibes. */}
      <div className="house-smoke house-smoke-1" aria-hidden="true" />
      <div className="house-smoke house-smoke-2" aria-hidden="true" />
      <div className="house-smoke house-smoke-3" aria-hidden="true" />

      <svg
        className="house-nav-svg"
        viewBox="0 0 260 220"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        {/* grass mound */}
        <ellipse cx="130" cy="212" rx="150" ry="16" className="house-grass" />

        {/* chimney (behind the roof so the pitch sits on top of it) */}
        <rect x="190" y="42" width="16" height="46" rx="2" className="house-chimney" />
        <rect x="188" y="40" width="20" height="6" rx="1" className="house-chimney-cap" />

        {/* roof */}
        <polygon points="30,90 130,30 230,90" className="house-roof" />
        {/* roof rim / shadow */}
        <polygon points="30,90 230,90 230,96 30,96" className="house-roof-rim" />

        {/* body / walls */}
        <rect x="50" y="88" width="160" height="118" rx="4" className="house-wall" />

        {/* front porch / base stripe */}
        <rect x="48" y="200" width="164" height="8" rx="2" className="house-porch" />

        {/* wall siding — subtle horizontal grooves so it doesn't feel
            like a flat box. Super low opacity. */}
        <line x1="54" y1="115" x2="206" y2="115" className="house-siding" />
        <line x1="54" y1="140" x2="206" y2="140" className="house-siding" />
        <line x1="54" y1="170" x2="206" y2="170" className="house-siding" />

        {/* window frames — painted behind the interactive overlays so
            each window has a visible trim on the cottage even if the
            NavLink z-index story changes. */}
        <g className="house-window-frames">
          {/* top row — 3 small */}
          <rect x="57"  y="95"  width="38" height="32" rx="6" />
          <rect x="111" y="95"  width="38" height="32" rx="6" />
          <rect x="165" y="95"  width="38" height="32" rx="6" />
          {/* bottom row — 2 big */}
          <rect x="62"  y="145" width="62" height="46" rx="8" />
          <rect x="136" y="145" width="62" height="46" rx="8" />
        </g>
      </svg>

      {/* Interactive windows — one NavLink per surface, positioned on
          top of the painted cottage. */}
      {WINDOWS.map(({ to, end, label, emoji, x, y, size }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            `house-window house-window-${size} ${isActive ? "lit" : ""}`
          }
          style={{ left: `${x}%`, top: `${y}%` }}
          title={label}
        >
          <span className="house-window-glow" aria-hidden="true" />
          <span className="house-window-emoji" aria-hidden="true">
            {emoji}
          </span>
          <span className="house-window-label">{label}</span>
        </NavLink>
      ))}
    </div>
  );
}
