import { NavLink } from "react-router-dom";
import "./MascotDock.css";

/**
 * Prototype nav — swap for the topbar pills.
 *
 * A fixed-bottom dock of five big mascot tiles, each with a soft
 * colored background drawn from the sherbet palette. Active tile
 * scales + saturates; hover bounces. No labels by default (they
 * surface as tooltips on hover) so the dock stays character-first.
 *
 * Built as its own component so the full prototype can be reverted
 * by removing its import in Sidebar/Layout + deleting these two
 * files. No other surface depends on it.
 */

const TILES = [
  { to: "/",            end: true, label: "Home",        emoji: "🏡", tone: "pink" },
  { to: "/tasks",                  label: "Tasks",       emoji: "📋", tone: "lavender" },
  { to: "/agents",                 label: "Pack",        emoji: "🐾", tone: "peach" },
  { to: "/knowledge",              label: "Knowledge",   emoji: "🧠", tone: "mint" },
  { to: "/automations",            label: "Automations", emoji: "⚡", tone: "lemon" },
];


export default function MascotDock() {
  return (
    <div className="mascot-dock" role="navigation" aria-label="Primary">
      <div className="mascot-dock-inner">
        {TILES.map(({ to, end, label, emoji, tone }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `mascot-tile tone-${tone} ${isActive ? "active" : ""}`
            }
            title={label}
          >
            <span className="mascot-tile-emoji" aria-hidden="true">
              {emoji}
            </span>
            <span className="mascot-tile-label">{label}</span>
          </NavLink>
        ))}
      </div>
    </div>
  );
}
