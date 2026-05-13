// Planet Maiko icon set — chunky pixel art in the Earthbound spirit.
//
// Designed as a drop-in for lucide-react: each icon is a named React
// component that accepts `size` (default 14), `color` (default
// currentColor), and `className`. Swap `import { Bot } from "lucide-
// react"` for `import { Paw } from "../icons"` and the props stay the
// same.
//
// Native viewBox is 16×16 so each `<rect width="1" height="1" />` is a
// real pixel. shape-rendering="crispEdges" + integer coords keep the
// art chunky at every render size, which is the whole point.

import "./icons.css";

function MIconBase({ size = 14, color = "currentColor", className = "", children }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill={color}
      shapeRendering="crispEdges"
      className={`m-icon ${className}`.trim()}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

// Pack — a paw print. Two big inner toes, two smaller outer toes,
// chunky palm pad. The most Maiko-coded icon in the set.
export function Paw(props) {
  return (
    <MIconBase {...props}>
      {/* inner toes */}
      <rect x="5" y="3" width="2" height="2" />
      <rect x="9" y="3" width="2" height="2" />
      {/* outer toes */}
      <rect x="2" y="5" width="2" height="2" />
      <rect x="12" y="5" width="2" height="2" />
      {/* palm pad */}
      <rect x="5" y="9" width="6" height="1" />
      <rect x="4" y="10" width="8" height="2" />
      <rect x="5" y="12" width="6" height="1" />
    </MIconBase>
  );
}

// Home — cozy cottage silhouette. Pitched roof, a door, no chimney
// (chimney detail gets lost at 14px).
export function Hearth(props) {
  return (
    <MIconBase {...props}>
      {/* roof, getting wider each row toward the eaves */}
      <rect x="7" y="2" width="2" height="1" />
      <rect x="6" y="3" width="4" height="1" />
      <rect x="5" y="4" width="6" height="1" />
      <rect x="4" y="5" width="8" height="1" />
      <rect x="3" y="6" width="10" height="1" />
      <rect x="2" y="7" width="12" height="1" />
      {/* walls */}
      <rect x="3" y="8" width="1" height="6" />
      <rect x="12" y="8" width="1" height="6" />
      <rect x="3" y="13" width="10" height="1" />
      {/* door */}
      <rect x="6" y="10" width="2" height="4" />
    </MIconBase>
  );
}

// Tasks — open scroll / list with body lines. Reads as "things to do."
export function Scroll(props) {
  return (
    <MIconBase {...props}>
      {/* outer frame */}
      <rect x="3" y="2" width="10" height="1" />
      <rect x="3" y="13" width="10" height="1" />
      <rect x="2" y="3" width="1" height="10" />
      <rect x="13" y="3" width="1" height="10" />
      {/* body lines */}
      <rect x="4" y="5" width="7" height="1" />
      <rect x="4" y="7" width="8" height="1" />
      <rect x="4" y="9" width="6" height="1" />
      <rect x="4" y="11" width="7" height="1" />
    </MIconBase>
  );
}

// Knowledge — open book / tome. Two pages with a binding gutter down
// the middle. Solid silhouette so it reads at small sizes.
export function Tome(props) {
  return (
    <MIconBase {...props}>
      {/* gutter caps */}
      <rect x="7" y="3" width="2" height="1" />
      {/* page block, left */}
      <rect x="2" y="4" width="5" height="9" />
      {/* page block, right */}
      <rect x="9" y="4" width="5" height="9" />
      {/* page lines (negative space — drawn as gaps using the SVG's
          background showing through). Since this is filled art, instead
          add a couple of "spine" highlights with explicit fill="none". */}
      <rect x="7" y="4" width="2" height="9" fill="none" />
      {/* bottom edge tapers in slightly so it looks like a book, not a
          frame */}
      <rect x="3" y="13" width="3" height="1" />
      <rect x="10" y="13" width="3" height="1" />
    </MIconBase>
  );
}

// Automations — chunky lightning bolt. The Z shape reads as "trigger,"
// the staple icon for when→then automation.
export function Spark(props) {
  return (
    <MIconBase {...props}>
      <rect x="8" y="2" width="3" height="1" />
      <rect x="7" y="3" width="3" height="1" />
      <rect x="6" y="4" width="3" height="1" />
      <rect x="5" y="5" width="3" height="1" />
      <rect x="4" y="6" width="7" height="1" />
      <rect x="7" y="7" width="3" height="1" />
      <rect x="6" y="8" width="3" height="1" />
      <rect x="5" y="9" width="3" height="1" />
      <rect x="4" y="10" width="3" height="1" />
      <rect x="3" y="11" width="3" height="1" />
    </MIconBase>
  );
}
