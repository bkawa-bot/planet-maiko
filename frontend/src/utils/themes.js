// Custom theme runtime: given a theme JSON from /api/themes, inject a
// <style> tag with CSS var overrides scoped to [data-theme="custom:<id>"].
// Applying a theme is then just flipping documentElement.dataset.theme to
// "custom:<id>".
//
// Body background is a vertical gradient sourced from the theme's --bg
// and --bg-plain values (defined in index.css). The legacy
// world_background SVG injection was removed when the built-in themes
// switched from hill SVGs to gradients — custom themes inherit the
// same gradient body rule. world_background still rides along on the
// theme JSON for back-compat / future use, but the value is currently
// inert in the runtime.

const STYLE_ID = "custom-theme-css";

// When a custom theme doesn't set `topbar_gradient` / `pane_bg` (common for
// themes saved before those keys were added to the schema), fall back to
// the gradient/alpha matching the theme's declared world_background
// instead of inheriting from :root — which is the dark/night default and
// made every custom theme look like a night theme regardless of its vibe.
// Kept in sync by hand with the built-in theme definitions in index.css.
const WORLD_TOPBAR_GRADIENT = {
  night:     "linear-gradient(135deg, #0a1628 0%, #162244 50%, #1a3a48 100%)",
  day:       "linear-gradient(135deg, #87CEEB 0%, #B0D8E8 50%, #B8D8B8 100%)",
  morning:   "linear-gradient(135deg, #F5C6AA 0%, #F0D8C0 50%, #F8E8D0 100%)",
  sunset:    "linear-gradient(135deg, #1A1A2E 0%, #2D2040 35%, #6A3058 70%, #A04070 100%)",
};
const WORLD_PANE_BG = {
  night:     "rgba(45, 54, 60, 0.40)",
  day:       "rgba(242, 248, 242, 0.60)",
  morning:   "rgba(250, 246, 238, 0.62)",
  sunset:    "rgba(58, 34, 72, 0.52)",
};

// Theme keys that DON'T correspond to a real CSS var — they're just
// inputs the designer assembles into one. Skipped when emitting the
// CSS var block so we don't leak a bogus --topbar-stop-top-left rule.
const VIRTUAL_KEYS = new Set([
  "topbar_stop_top_left",
  "topbar_stop_middle_left",
  "topbar_stop_middle_right",
  "topbar_stop_top_right",
]);

// Snap stop keys onto the four gradient positions used by the
// topbar (135deg, top-left → bottom-right). Order matters.
const TOPBAR_STOP_KEYS = [
  "topbar_stop_top_left",
  "topbar_stop_middle_left",
  "topbar_stop_middle_right",
  "topbar_stop_top_right",
];

// Compose a 135deg linear-gradient from whichever of the four stop
// fields are set. Returns null when fewer than 2 stops are filled —
// a single color isn't a gradient, and the caller should fall back
// to the legacy topbar_gradient string in that case.
function composeStopsGradient(colors) {
  const filled = TOPBAR_STOP_KEYS
    .map((key) => (colors[key] || "").trim())
    .filter(Boolean);
  if (filled.length < 2) return null;
  const step = 100 / (filled.length - 1);
  const parts = filled.map((color, i) => {
    const pos = i === filled.length - 1 ? 100 : Math.round(i * step);
    return `${color} ${pos}%`;
  });
  return `linear-gradient(135deg, ${parts.join(", ")})`;
}

// snake_case key -> kebab CSS var name
const cssVarName = (key) => `--${key.replace(/_/g, "-")}`;

function escapeSelector(id) {
  // Theme IDs are validated server-side to [a-z0-9_-]+ so this is
  // mostly paranoia — still, let CSS escape colons and anything else.
  return (window.CSS && typeof window.CSS.escape === "function")
    ? window.CSS.escape(id)
    : id.replace(/[^a-zA-Z0-9_-]/g, "");
}

export function themeToCss(theme) {
  const selector = `[data-theme="custom:${escapeSelector(theme.id)}"]`;
  const colors = theme.colors || {};
  const world = theme.world_background;

  // Back-compat: themes saved before topbar_gradient / pane_bg were
  // part of the schema inherit the world-appropriate defaults instead
  // of :root's night values (which made every theme look like a dark
  // theme regardless of its declared vibe).
  const effectiveColors = { ...colors };

  // If the designer set any of the four topbar stop fields, compose
  // those into the gradient — overrides any topbar_gradient string
  // the theme had (including one the LLM generator wrote). Falling
  // back to topbar_gradient when fewer than two stops are filled
  // keeps LLM-generated themes working without any stops at all.
  const composed = composeStopsGradient(colors);
  if (composed) {
    effectiveColors.topbar_gradient = composed;
  }

  if (!effectiveColors.topbar_gradient && WORLD_TOPBAR_GRADIENT[world]) {
    effectiveColors.topbar_gradient = WORLD_TOPBAR_GRADIENT[world];
  }
  if (!effectiveColors.pane_bg && WORLD_PANE_BG[world]) {
    effectiveColors.pane_bg = WORLD_PANE_BG[world];
  }

  const varLines = Object.entries(effectiveColors)
    .filter(([k]) => !VIRTUAL_KEYS.has(k))
    .map(([k, v]) => `  ${cssVarName(k)}: ${v};`)
    .join("\n");

  return `${selector} {\n${varLines}\n}`.trim();
}

export function applyCustomTheme(theme) {
  let style = document.getElementById(STYLE_ID);
  if (!style) {
    style = document.createElement("style");
    style.id = STYLE_ID;
    document.head.appendChild(style);
  }
  style.textContent = themeToCss(theme);
  document.documentElement.setAttribute("data-theme", `custom:${theme.id}`);
  try {
    localStorage.setItem("maiko-theme", `custom:${theme.id}`);
    localStorage.setItem(`maiko-theme-cache:${theme.id}`, JSON.stringify(theme));
  } catch { /* quota / private mode — no-op */ }
}

export function clearCustomTheme() {
  const style = document.getElementById(STYLE_ID);
  if (style) style.remove();
}

// On early app boot, before any API call lands, try to re-apply the last
// custom theme from the localStorage cache so there's no flash of default
// colors. Safe to call with any theme id — bails if there's no cached
// payload for it.
export function hydrateCachedCustomTheme(themeId) {
  if (!themeId || !themeId.startsWith("custom:")) return false;
  const id = themeId.slice("custom:".length);
  try {
    const raw = localStorage.getItem(`maiko-theme-cache:${id}`);
    if (!raw) return false;
    applyCustomTheme(JSON.parse(raw));
    return true;
  } catch {
    return false;
  }
}
