// Planet Maiko icon set — chunky pixel art in the Earthbound spirit.
//
// Designed as a drop-in superset of lucide-react: each icon is a named
// React component that accepts `size` (default 14), `color` (default
// currentColor), and `className`. The bottom of the file re-exports
// every lucide icon we haven't ported yet, so callers import everything
// from "@icons" and the customized ones override lucide's by name.
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

// ============================================================
// Named after their metaphor — used in the nav today, useful
// elsewhere. Import as `import { Paw } from "@icons"`.
// ============================================================

// Pack — a paw print. Two big inner toes, two smaller outer toes,
// chunky palm pad. The most Maiko-coded icon in the set.
export function Paw(props) {
  return (
    <MIconBase {...props}>
      <rect x="5" y="3" width="2" height="2" />
      <rect x="9" y="3" width="2" height="2" />
      <rect x="2" y="5" width="2" height="2" />
      <rect x="12" y="5" width="2" height="2" />
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
      <rect x="7" y="2" width="2" height="1" />
      <rect x="6" y="3" width="4" height="1" />
      <rect x="5" y="4" width="6" height="1" />
      <rect x="4" y="5" width="8" height="1" />
      <rect x="3" y="6" width="10" height="1" />
      <rect x="2" y="7" width="12" height="1" />
      <rect x="3" y="8" width="1" height="6" />
      <rect x="12" y="8" width="1" height="6" />
      <rect x="3" y="13" width="10" height="1" />
      <rect x="6" y="10" width="2" height="4" />
    </MIconBase>
  );
}

// Tasks — bordered list with body lines. Reads as "things to do."
export function Scroll(props) {
  return (
    <MIconBase {...props}>
      <rect x="3" y="2" width="10" height="1" />
      <rect x="3" y="13" width="10" height="1" />
      <rect x="2" y="3" width="1" height="10" />
      <rect x="13" y="3" width="1" height="10" />
      <rect x="4" y="5" width="7" height="1" />
      <rect x="4" y="7" width="8" height="1" />
      <rect x="4" y="9" width="6" height="1" />
      <rect x="4" y="11" width="7" height="1" />
    </MIconBase>
  );
}

// Knowledge — open book / tome with a gutter down the middle.
export function Tome(props) {
  return (
    <MIconBase {...props}>
      <rect x="7" y="3" width="2" height="1" />
      <rect x="2" y="4" width="5" height="9" />
      <rect x="9" y="4" width="5" height="9" />
      <rect x="3" y="13" width="3" height="1" />
      <rect x="10" y="13" width="3" height="1" />
    </MIconBase>
  );
}

// Automations — chunky lightning bolt Z.
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

// ============================================================
// Customized icons that REPLACE the lucide-react versions of the
// same name. Anything importing { X, Plus, Check, ... } from
// "@icons" gets the Maiko pixel-art version; everything else falls
// through to lucide via the `export * from` line at the bottom.
// ============================================================

// X — chunky diagonal cross, used everywhere as "close / dismiss."
export function X(props) {
  return (
    <MIconBase {...props}>
      <rect x="3" y="3" width="2" height="2" />
      <rect x="11" y="3" width="2" height="2" />
      <rect x="4" y="4" width="2" height="2" />
      <rect x="10" y="4" width="2" height="2" />
      <rect x="5" y="5" width="2" height="2" />
      <rect x="9" y="5" width="2" height="2" />
      <rect x="6" y="6" width="4" height="4" />
      <rect x="5" y="9" width="2" height="2" />
      <rect x="9" y="9" width="2" height="2" />
      <rect x="4" y="10" width="2" height="2" />
      <rect x="10" y="10" width="2" height="2" />
      <rect x="3" y="11" width="2" height="2" />
      <rect x="11" y="11" width="2" height="2" />
    </MIconBase>
  );
}

// Plus — chunky + sign for "create / add."
export function Plus(props) {
  return (
    <MIconBase {...props}>
      <rect x="6" y="3" width="2" height="10" />
      <rect x="3" y="6" width="8" height="2" />
      {/* second pair to make the cross center square — visual
          balance against the X icon at the same size */}
      <rect x="6" y="3" width="2" height="10" />
      <rect x="3" y="6" width="8" height="2" />
    </MIconBase>
  );
}

// Check — short bottom-left arm + long top-right arm.
export function Check(props) {
  return (
    <MIconBase {...props}>
      <rect x="12" y="3" width="2" height="1" />
      <rect x="11" y="4" width="2" height="1" />
      <rect x="10" y="5" width="2" height="1" />
      <rect x="9" y="6" width="2" height="1" />
      <rect x="8" y="7" width="2" height="1" />
      <rect x="7" y="8" width="2" height="1" />
      <rect x="6" y="9" width="2" height="1" />
      <rect x="2" y="8" width="2" height="1" />
      <rect x="3" y="9" width="2" height="1" />
      <rect x="4" y="10" width="2" height="1" />
      <rect x="5" y="11" width="2" height="1" />
    </MIconBase>
  );
}

// Loader — 3/4 ring with a gap in the upper-right quadrant. The
// .spin utility class (defined in index.css) rotates the whole SVG,
// so the gap chases around like a classic spinner.
export function Loader(props) {
  return (
    <MIconBase {...props}>
      <rect x="3" y="2" width="5" height="1" />
      <rect x="2" y="3" width="3" height="1" />
      <rect x="1" y="4" width="3" height="1" />
      <rect x="1" y="5" width="2" height="2" />
      <rect x="2" y="11" width="2" height="2" />
      <rect x="1" y="9" width="2" height="2" />
      <rect x="3" y="13" width="3" height="1" />
      <rect x="5" y="13" width="6" height="1" />
      <rect x="10" y="12" width="3" height="1" />
      <rect x="12" y="10" width="2" height="2" />
      <rect x="13" y="7" width="2" height="3" />
    </MIconBase>
  );
}

// ChevronDown — solid filled triangle pointing down.
export function ChevronDown(props) {
  return (
    <MIconBase {...props}>
      <rect x="2" y="5" width="12" height="1" />
      <rect x="3" y="6" width="10" height="1" />
      <rect x="4" y="7" width="8" height="1" />
      <rect x="5" y="8" width="6" height="1" />
      <rect x="6" y="9" width="4" height="1" />
      <rect x="7" y="10" width="2" height="1" />
    </MIconBase>
  );
}

// ChevronRight — solid filled triangle pointing right.
export function ChevronRight(props) {
  return (
    <MIconBase {...props}>
      <rect x="5" y="2" width="1" height="12" />
      <rect x="6" y="3" width="1" height="10" />
      <rect x="7" y="4" width="1" height="8" />
      <rect x="8" y="5" width="1" height="6" />
      <rect x="9" y="6" width="1" height="4" />
      <rect x="10" y="7" width="1" height="2" />
    </MIconBase>
  );
}

// Settings — chunky 4-tooth gear with a square hole.
export function Settings(props) {
  return (
    <MIconBase {...props}>
      {/* N tooth */}
      <rect x="6" y="1" width="2" height="2" />
      {/* S tooth */}
      <rect x="6" y="13" width="2" height="2" />
      {/* W tooth */}
      <rect x="1" y="6" width="2" height="2" />
      {/* E tooth */}
      <rect x="13" y="6" width="2" height="2" />
      {/* body — squared off cog */}
      <rect x="4" y="3" width="6" height="1" />
      <rect x="3" y="4" width="8" height="1" />
      <rect x="3" y="5" width="2" height="6" />
      <rect x="9" y="5" width="2" height="6" />
      <rect x="3" y="11" width="8" height="1" />
      <rect x="4" y="12" width="6" height="1" />
      {/* connect top/bottom rims to give the cog a closed look around
          the hole */}
      <rect x="5" y="5" width="4" height="1" />
      <rect x="5" y="10" width="4" height="1" />
    </MIconBase>
  );
}

// Power — short vertical bar over a ring with an opening at the top.
export function Power(props) {
  return (
    <MIconBase {...props}>
      {/* vertical bar */}
      <rect x="7" y="2" width="2" height="6" />
      {/* ring — top arc with a notch at top-center */}
      <rect x="4" y="4" width="2" height="1" />
      <rect x="10" y="4" width="2" height="1" />
      <rect x="3" y="5" width="2" height="2" />
      <rect x="11" y="5" width="2" height="2" />
      <rect x="2" y="6" width="2" height="6" />
      <rect x="12" y="6" width="2" height="6" />
      <rect x="3" y="11" width="2" height="2" />
      <rect x="11" y="11" width="2" height="2" />
      <rect x="4" y="12" width="2" height="1" />
      <rect x="10" y="12" width="2" height="1" />
      <rect x="5" y="13" width="6" height="1" />
    </MIconBase>
  );
}

// Palette — tilted ovoid with three paint dots and a thumb opening.
export function Palette(props) {
  return (
    <MIconBase {...props}>
      {/* outer ovoid */}
      <rect x="4" y="2" width="6" height="1" />
      <rect x="3" y="3" width="8" height="1" />
      <rect x="2" y="4" width="2" height="6" />
      <rect x="10" y="4" width="2" height="3" />
      <rect x="3" y="10" width="2" height="2" />
      <rect x="4" y="12" width="3" height="1" />
      <rect x="7" y="13" width="3" height="1" />
      <rect x="10" y="12" width="2" height="1" />
      <rect x="11" y="11" width="2" height="1" />
      <rect x="12" y="10" width="1" height="1" />
      {/* paint dots */}
      <rect x="5" y="5" width="2" height="2" />
      <rect x="9" y="5" width="1" height="1" />
      <rect x="5" y="9" width="1" height="1" />
      <rect x="8" y="9" width="2" height="2" />
    </MIconBase>
  );
}

// Leaf — teardrop with a stem at the bottom-left.
export function Leaf(props) {
  return (
    <MIconBase {...props}>
      <rect x="10" y="2" width="2" height="1" />
      <rect x="9" y="3" width="3" height="1" />
      <rect x="8" y="4" width="5" height="1" />
      <rect x="7" y="5" width="6" height="1" />
      <rect x="6" y="6" width="7" height="1" />
      <rect x="5" y="7" width="7" height="1" />
      <rect x="4" y="8" width="7" height="1" />
      <rect x="3" y="9" width="7" height="1" />
      <rect x="3" y="10" width="6" height="1" />
      <rect x="3" y="11" width="4" height="1" />
      <rect x="4" y="12" width="2" height="1" />
      <rect x="5" y="13" width="2" height="1" />
    </MIconBase>
  );
}

// Sparkles — one large 4-pointed star + two small ones at corners,
// matching lucide's Sparkles silhouette.
export function Sparkles(props) {
  return (
    <MIconBase {...props}>
      {/* center large star */}
      <rect x="8" y="4" width="1" height="8" />
      <rect x="7" y="5" width="3" height="6" />
      <rect x="4" y="7" width="9" height="2" />
      <rect x="6" y="6" width="5" height="4" />
      {/* small star, top-right */}
      <rect x="13" y="2" width="1" height="3" />
      <rect x="12" y="3" width="3" height="1" />
      {/* small star, bottom-left */}
      <rect x="2" y="12" width="3" height="1" />
      <rect x="3" y="11" width="1" height="3" />
    </MIconBase>
  );
}

// ============================================================
// Pass-through for everything we haven't ported yet. `export *` would
// be tidier but lucide-react's package shape doesn't propagate cleanly
// through rolldown's wildcard, so we list explicitly. Add the icon
// here when a new file pulls something we don't already re-export.
// Putting our customized icons above this block means imports of
// `X` / `Plus` / `Loader` / etc. resolve to the pixel-art versions.
// ============================================================
export {
  Activity, AlertCircle, AlertTriangle, ArrowLeft, ArrowRight, Bell,
  BookOpen, Bot, Brain, Bug, Calendar, CheckCircle2, CheckSquare,
  ChevronUp, Circle, Clipboard, ClipboardCheck, Clock, Code2, Coffee,
  Compass, Database, Download, Edit3, ExternalLink, Eye, FileText,
  Flame, Folder, FolderGit2, FolderOpen, FolderPlus, GitBranch,
  GitFork, GitPullRequest, HeartPulse, HelpCircle, Home, Inbox, Layers,
  ListTodo, Map, MapPin, MessageCircle, MessageSquare, Moon,
  MoreHorizontal, PanelRightClose, PanelRightOpen, Pause, PawPrint,
  Pencil, Pin, PinOff, Play, Plug, RefreshCw, Rocket, RotateCcw, Save,
  Search, Send, Shield, Square, Sunrise, Target, Trash2, Upload, User,
  Video, Wand2, Zap,
} from "lucide-react";
