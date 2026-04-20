// Custom theme runtime: given a theme JSON from /api/themes, inject a
// <style> tag with CSS var overrides scoped to [data-theme="custom:<id>"]
// and the matching world-background rule. Applying a theme is then just
// flipping documentElement.dataset.theme to "custom:<id>".

const STYLE_ID = "custom-theme-css";
const WORLD_SVG = {
  night: "/world-night.svg",
  day: "/world-day.svg",
  morning: "/world-morning.svg",
  sunset: "/world-sunset.svg",
};

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
  if (!effectiveColors.topbar_gradient && WORLD_TOPBAR_GRADIENT[world]) {
    effectiveColors.topbar_gradient = WORLD_TOPBAR_GRADIENT[world];
  }
  if (!effectiveColors.pane_bg && WORLD_PANE_BG[world]) {
    effectiveColors.pane_bg = WORLD_PANE_BG[world];
  }

  const varLines = Object.entries(effectiveColors)
    .map(([k, v]) => `  ${cssVarName(k)}: ${v};`)
    .join("\n");

  let worldRule = "";
  if (world === "none") {
    worldRule = `html${selector} body { background-image: none; }`;
  } else if (WORLD_SVG[world]) {
    worldRule = `html${selector} body { background-image: url('${WORLD_SVG[world]}'); }`;
  }
  return `${selector} {\n${varLines}\n}\n${worldRule}`.trim();
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
