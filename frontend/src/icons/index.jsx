// Planet Maiko icon set — backed by game-icons.net (CC BY 3.0).
//
// Same API as before (size, color, className, currentColor by default)
// so the bulk-swap from "lucide-react" → "@icons" still works. Each
// named export is a thin wrapper around Iconify's <Icon> that maps to
// a specific game-icons identifier. Game-icons is fantasy / tarot-y
// line art — distinct from lucide's modern outline style, on-brand
// for "strange agents, strange world."
//
// Attribution lives in the LICENSES file: each icon credits its
// original artist on game-icons.net under CC BY 3.0.

import { Icon, addCollection } from "@iconify/react";
import gameIcons from "@iconify-json/game-icons/icons.json";
import "./icons.css";

// Register the full game-icons collection once. ~4000 icons; tree-
// shaking can't strip them since lookups are by string, so the JSON
// ships in full. That's the cost of using a curated set with a clean
// lookup API. For a Tauri-first app the bundle hit is fine.
addCollection(gameIcons);

// Wrapper: adapts game-icons (which size from a large native viewBox
// and rotate via prop) to our props. Game-icons render with `fill` =
// currentColor by default, so the `color` prop just sets text color
// on the wrapping element and the SVG inherits.
function MIcon({ icon, size = 14, color = "currentColor", className = "", rotate }) {
  return (
    <Icon
      icon={`game-icons:${icon}`}
      width={size}
      height={size}
      style={{ color }}
      className={`m-icon ${className}`.trim()}
      rotate={rotate}
      aria-hidden="true"
    />
  );
}

// ============================================================
// Customized identity icons — the names we use in nav + chrome.
// ============================================================

export const Paw = (p) => <MIcon icon="paw-print" {...p} />;
export const Hearth = (p) => <MIcon icon="house" {...p} />;
export const Scroll = (p) => <MIcon icon="scroll-unfurled" {...p} />;
export const Tome = (p) => <MIcon icon="book-cover" {...p} />;
export const Spark = (p) => <MIcon icon="lightning-arc" {...p} />;

// ============================================================
// Replacements for lucide names. Anything imported from "@icons"
// under one of these names gets the game-icons version.
// ============================================================

// Verbs
export const X = (p) => <MIcon icon="cancel" {...p} />;
export const Check = (p) => <MIcon icon="check-mark" {...p} />;
export const Send = (p) => <MIcon icon="paper-plane" {...p} />;
export const Save = (p) => <MIcon icon="save-arrow" {...p} />;
export const Trash2 = (p) => <MIcon icon="trash-can" {...p} />;
export const Pencil = (p) => <MIcon icon="quill-ink" {...p} />;
export const Edit3 = (p) => <MIcon icon="quill-ink" {...p} />;
export const Search = (p) => <MIcon icon="magnifying-glass" {...p} />;
export const Eye = (p) => <MIcon icon="all-seeing-eye" {...p} />;
export const Play = (p) => <MIcon icon="play-button" {...p} />;
export const Pause = (p) => <MIcon icon="pause-button" {...p} />;
export const Download = (p) => <MIcon icon="cloud-download" {...p} />;
export const Upload = (p) => <MIcon icon="cloud-upload" {...p} />;
export const RefreshCw = (p) => <MIcon icon="cycle" {...p} />;
export const RotateCcw = (p) => <MIcon icon="cycle" {...p} />;
export const Loader = (p) => <MIcon icon="spinning-top" {...p} />;
export const Pin = (p) => <MIcon icon="pin" {...p} />;
export const PinOff = (p) => <MIcon icon="pin" {...p} />;

// Topbar / chrome
export const Settings = (p) => <MIcon icon="cog" {...p} />;
export const Power = (p) => <MIcon icon="power-button" {...p} />;
export const Palette = (p) => <MIcon icon="palette" {...p} />;
export const Sparkles = (p) => <MIcon icon="sparkles" {...p} />;
export const Bell = (p) => <MIcon icon="ringing-bell" {...p} />;
export const Megaphone = (p) => <MIcon icon="megaphone" {...p} />;
// User-picked game-icons swaps (Automations, Knowledge, Today, RAG,
// categories, action, ask-the-pack, gathering).
export const Crystal = (p) => <MIcon icon="crystal-shine" {...p} />;
export const Sun = (p) => <MIcon icon="sun" {...p} />;
export const SpellBook = (p) => <MIcon icon="spell-book" {...p} />;
export const CheckboxTree = (p) => <MIcon icon="checkbox-tree" {...p} />;
export const LogicGateNot = (p) => <MIcon icon="logic-gate-not" {...p} />;
export const Conversation = (p) => <MIcon icon="conversation" {...p} />;
export const BowenKnot = (p) => <MIcon icon="bowen-knot" {...p} />;
export const MoonTarot = (p) => <MIcon icon="tarot-18-the-moon" {...p} />;
export const RadarSweep = (p) => <MIcon icon="radar-sweep" {...p} />;
export const CombinationLock = (p) => <MIcon icon="combination-lock" {...p} />;
export const Processor = (p) => <MIcon icon="processor" {...p} />;
export const ChatBubble = (p) => <MIcon icon="chat-bubble" {...p} />;
export const HelpCircle = (p) => <MIcon icon="help" {...p} />;
export const MoreHorizontal = (p) => <MIcon icon="three-leaves" {...p} />;

// Nature / scene
export const Leaf = (p) => <MIcon icon="linden-leaf" {...p} />;
export const Flame = (p) => <MIcon icon="flame" {...p} />;
export const Moon = (p) => <MIcon icon="moon" {...p} />;
export const Sunrise = (p) => <MIcon icon="sunrise" {...p} />;
export const Coffee = (p) => <MIcon icon="coffee-cup" {...p} />;

// Map / wayfinding
export const Map = (p) => <MIcon icon="treasure-map" {...p} />;
export const MapPin = (p) => <MIcon icon="position-marker" {...p} />;
export const Compass = (p) => <MIcon icon="compass" {...p} />;
export const Target = (p) => <MIcon icon="on-target" {...p} />;

// Time
export const Clock = (p) => <MIcon icon="alarm-clock" {...p} />;
export const Calendar = (p) => <MIcon icon="calendar" {...p} />;
export const Activity = (p) => <MIcon icon="pulse" {...p} />;
export const HeartPulse = (p) => <MIcon icon="heart-beats" {...p} />;

// Talk / messaging
export const MessageSquare = (p) => <MIcon icon="talk" {...p} />;
export const MessageCircle = (p) => <MIcon icon="talk" {...p} />;
export const User = (p) => <MIcon icon="hooded-figure" {...p} />;

// Pack / agents
export const Bot = (p) => <MIcon icon="wolf-head" {...p} />;
export const Brain = (p) => <MIcon icon="brain" {...p} />;
export const PawPrint = (p) => <MIcon icon="paw-print" {...p} />;
export const Wand2 = (p) => <MIcon icon="fairy-wand" {...p} />;
export const Shield = (p) => <MIcon icon="shield" {...p} />;
export const Rocket = (p) => <MIcon icon="rocket" {...p} />;
export const Bug = (p) => <MIcon icon="ladybug" {...p} />;

// Files / repo
export const Folder = (p) => <MIcon icon="open-folder" {...p} />;
export const FolderOpen = (p) => <MIcon icon="open-folder" {...p} />;
export const FolderPlus = (p) => <MIcon icon="open-folder" {...p} />;
export const FolderGit2 = (p) => <MIcon icon="open-folder" {...p} />;
export const FileText = (p) => <MIcon icon="scroll-unfurled" {...p} />;
export const Clipboard = (p) => <MIcon icon="scroll-quill" {...p} />;
export const ClipboardCheck = (p) => <MIcon icon="checklist" {...p} />;
export const ListTodo = (p) => <MIcon icon="checklist" {...p} />;
export const BookOpen = (p) => <MIcon icon="open-book" {...p} />;
export const Inbox = (p) => <MIcon icon="chest" {...p} />;
export const Database = (p) => <MIcon icon="database" {...p} />;
export const Layers = (p) => <MIcon icon="layered-armor" {...p} />;
export const Code2 = (p) => <MIcon icon="gears" {...p} />;

// Git
export const GitBranch = (p) => <MIcon icon="tree-branch" {...p} />;
export const GitFork = (p) => <MIcon icon="tree-roots" {...p} />;
export const GitPullRequest = (p) => <MIcon icon="scroll-quill" {...p} />;

// Misc
export const Plug = (p) => <MIcon icon="plug" {...p} />;
export const Video = (p) => <MIcon icon="film-projector" {...p} />;
export const Zap = (p) => <MIcon icon="lightning-arc" {...p} />;
export const Home = (p) => <MIcon icon="house" {...p} />;
export const Circle = (p) => <MIcon icon="plain-circle" {...p} />;
export const Square = (p) => <MIcon icon="plain-square" {...p} />;
export const CheckCircle2 = (p) => <MIcon icon="check-mark" {...p} />;
export const CheckSquare = (p) => <MIcon icon="checklist" {...p} />;

// Warnings — game-icons doesn't have a clean exclamation or warning
// triangle; hazard-sign reads as "alert" without being literal.
export const AlertCircle = (p) => <MIcon icon="hazard-sign" {...p} />;
export const AlertTriangle = (p) => <MIcon icon="hazard-sign" {...p} />;

// Arrows — game-icons `plain-arrow` points RIGHT by default. So
// ArrowRight is the native glyph (no rotate) and ArrowLeft is a 180°
// turn. The old rotate={3}/{1} pair was anchored to a wrong base and
// rendered Left/Right pointing up/down — hence the backwards Back
// button.
export const ArrowLeft = (p) => <MIcon icon="plain-arrow" rotate={2} {...p} />;
export const ArrowRight = (p) => <MIcon icon="plain-arrow" {...p} />;
export const ExternalLink = (p) => <MIcon icon="arrow-cursor" {...p} />;
export const PanelRightOpen = (p) => <MIcon icon="arrow-cursor" {...p} />;
export const PanelRightClose = (p) => <MIcon icon="arrow-cursor" rotate={2} {...p} />;

// ============================================================
// Chevrons + Plus — game-icons has no clean equivalents that read
// at 12-15px sizes (lucide's minimalist triangles are unmatched
// in the fantasy line-art idiom). Kept as pixel-art so dropdowns
// and disclosure triangles stay legible. Same MIconBase wrapper
// as before, just inlined here so callers don't need a second
// import path.
// ============================================================

function PixelIcon({ size = 14, color = "currentColor", className = "", children }) {
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

export const ChevronDown = (props) => (
  <PixelIcon {...props}>
    <rect x="2" y="5" width="12" height="1" />
    <rect x="3" y="6" width="10" height="1" />
    <rect x="4" y="7" width="8" height="1" />
    <rect x="5" y="8" width="6" height="1" />
    <rect x="6" y="9" width="4" height="1" />
    <rect x="7" y="10" width="2" height="1" />
  </PixelIcon>
);

export const ChevronRight = (props) => (
  <PixelIcon {...props}>
    <rect x="5" y="2" width="1" height="12" />
    <rect x="6" y="3" width="1" height="10" />
    <rect x="7" y="4" width="1" height="8" />
    <rect x="8" y="5" width="1" height="6" />
    <rect x="9" y="6" width="1" height="4" />
    <rect x="10" y="7" width="1" height="2" />
  </PixelIcon>
);

export const ChevronUp = (props) => (
  <PixelIcon {...props}>
    <rect x="7" y="5" width="2" height="1" />
    <rect x="6" y="6" width="4" height="1" />
    <rect x="5" y="7" width="6" height="1" />
    <rect x="4" y="8" width="8" height="1" />
    <rect x="3" y="9" width="10" height="1" />
    <rect x="2" y="10" width="12" height="1" />
  </PixelIcon>
);

export const Plus = (props) => (
  <PixelIcon {...props}>
    <rect x="6" y="3" width="2" height="10" />
    <rect x="3" y="6" width="8" height="2" />
    <rect x="6" y="3" width="2" height="10" />
    <rect x="3" y="6" width="8" height="2" />
  </PixelIcon>
);
