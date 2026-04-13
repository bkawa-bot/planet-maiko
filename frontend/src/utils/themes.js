// Custom theme runtime: given a theme JSON from /api/themes, inject a
// <style> tag with CSS var overrides scoped to [data-theme="custom:<id>"]
// and the matching world-background rule. Applying a theme is then just
// flipping documentElement.dataset.theme to "custom:<id>".

const STYLE_ID = "custom-theme-css";
const WORLD_SVG = {
  night: "/world-night.svg",
  day: "/world-day.svg",
  morning: "/world-morning.svg",
  afternoon: "/world-afternoon.svg",
  sunset: "/world-sunset.svg",
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
  const varLines = Object.entries(theme.colors || {})
    .map(([k, v]) => `  ${cssVarName(k)}: ${v};`)
    .join("\n");
  const world = theme.world_background;
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
