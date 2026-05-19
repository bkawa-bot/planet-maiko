import { NavLink, useNavigate } from "react-router-dom";
import { Settings, Palette, Power, Hearth, ListTodo, Paw, SpellBook, Crystal } from "@icons";
import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import { applyCustomTheme, clearCustomTheme, hydrateCachedCustomTheme } from "../utils/themes";
import SystemHealth from "./SystemHealth";
import "./Sidebar.css";
import "./ShutdownModal.css";

// Nav icons come from the Maiko pixel-art set (icons/index.jsx) — paw
// for the Pack, scroll for Tasks, etc. The rest of the topbar (gear,
// power, palette) still uses lucide while those surfaces
// wait their turn for the pixel-art treatment.
const NAV_ITEMS = [
  { to: "/", icon: Hearth, label: "Home", end: true },
  { to: "/tasks", icon: ListTodo, label: "Tasks" },
  { to: "/agents", icon: Paw, label: "Pack" },
  { to: "/knowledge", icon: SpellBook, label: "Knowledge" },
  { to: "/automations", icon: Crystal, label: "Automations" },
];

// Built-in theme palette. Each entry's `group` controls dropdown
// section dividers — "auto" sits up top on its own, then the four
// time-of-day groups in dawn → night order. Adding a new theme is a
// matter of adding a row here and a [data-theme="<id>"] block in
// index.css with the matching color tokens.
const THEMES = [
  { id: "auto", label: "Auto", emoji: "🔄", group: "auto" },
  // Night — cosmic, bioluminescent, frozen, magical
  { id: "dark", label: "Cosmic Nighttime", emoji: "🌙", group: "night" },
  { id: "bioluminescent", label: "Bioluminescent", emoji: "🟢", group: "night" },
  { id: "frozen", label: "Frozen", emoji: "❄️", group: "night" },
  // Twilight — coral horizon over deep indigo
  { id: "sunset", label: "Twilight", emoji: "🌇", group: "twilight" },
  // Morning — alien sunrise haze
  { id: "morning", label: "Dawn", emoji: "🌅", group: "morning" },
  // Day — bright iced sky + mint meadow
  { id: "light", label: "Daylight", emoji: "☀️", group: "day" },
  { id: "mint-meadow", label: "Mint Meadow", emoji: "🌿", group: "day" },
];

function getAutoTheme() {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 17) return "light";
  if (hour >= 17 && hour < 20) return "sunset";
  return "dark";
}

export default function Sidebar({ onOpenShutdown }) {
  const navigate = useNavigate();
  const [theme, setTheme] = useState(() => {
    const saved = localStorage.getItem("maiko-theme") || "dark";
    // Re-apply cached custom theme ASAP so the first paint uses the right
    // colors instead of flashing the default dark theme.
    hydrateCachedCustomTheme(saved);
    return saved;
  });
  const [customThemes, setCustomThemes] = useState([]);
  const [showThemeMenu, setShowThemeMenu] = useState(false);
  const themeRef = useRef(null);

  useEffect(() => {
    if (theme.startsWith("custom:")) {
      const id = theme.slice("custom:".length);
      const cached = customThemes.find((t) => t.id === id);
      if (cached) {
        applyCustomTheme(cached);
      } else {
        // Fetch if we don't have it yet (e.g. first load before the
        // customThemes list arrives). hydrateCachedCustomTheme already
        // handled the very-first render from localStorage.
        api.getTheme(id).then(applyCustomTheme).catch(() => {
          // Theme was deleted externally — fall back to dark.
          setTheme("dark");
        });
      }
      localStorage.setItem("maiko-theme", theme);
      return undefined;
    }

    clearCustomTheme();
    const resolved = theme === "auto" ? getAutoTheme() : theme;
    document.documentElement.setAttribute("data-theme", resolved);
    localStorage.setItem("maiko-theme", theme);
    if (theme === "auto") {
      const interval = setInterval(() => {
        document.documentElement.setAttribute("data-theme", getAutoTheme());
      }, 300000);
      return () => clearInterval(interval);
    }
    return undefined;
  }, [theme, customThemes]);

  useEffect(() => {
    api.getThemes().then(setCustomThemes).catch(() => {});
  }, []);

  useEffect(() => {
    const handleClick = (e) => {
      if (themeRef.current && !themeRef.current.contains(e.target)) setShowThemeMenu(false);
    };
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, []);

  const activeCustomTheme = theme.startsWith("custom:")
    ? customThemes.find((t) => t.id === theme.slice("custom:".length))
    : null;
  const themeEmoji = activeCustomTheme?.emoji
    || THEMES.find((t) => t.id === theme)?.emoji
    || "🌙";

  return (
    <>
      {/* Frosted top bar — brand + utilities */}
      <div className="topbar">
        <div className="topbar-left">
          <NavLink to="/" className="topbar-logo">
            <img src="/icon.svg" alt="Maiko" className="topbar-icon" />
            <div className="topbar-logo-text">
              <span className="topbar-planet">PLANET</span>
              <span className="topbar-maiko">MAIKO</span>
            </div>
          </NavLink>

          <nav className="topbar-nav">
            {NAV_ITEMS.map(({ to, icon: Icon, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) => `nav-pill ${isActive ? "active" : ""}`}
              >
                <Icon size={15} className="nav-pill-icon" />
                <span className="nav-pill-label">{label}</span>
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="topbar-right">
          <div className="theme-wrapper" ref={themeRef}>
            <button className="topbar-action" onClick={() => setShowThemeMenu(!showThemeMenu)} title="Theme">
              {themeEmoji}
            </button>
            {showThemeMenu && (
              <div className="topbar-dropdown">
                {(() => {
                  // Render the built-in themes with a divider between
                  // each `group` change, so the 14-item list reads as
                  // four daypart sections under "Auto" instead of one
                  // long blur.
                  const out = [];
                  let lastGroup = null;
                  THEMES.forEach((t) => {
                    if (lastGroup !== null && t.group !== lastGroup) {
                      out.push(<div key={`div-${t.group}`} className="dropdown-divider" />);
                    }
                    out.push(
                      <button
                        key={t.id}
                        className={`dropdown-item ${theme === t.id ? "active" : ""}`}
                        onClick={() => { setTheme(t.id); setShowThemeMenu(false); }}
                      >
                        <span>{t.emoji}</span> {t.label}
                      </button>,
                    );
                    lastGroup = t.group;
                  });
                  return out;
                })()}
                {customThemes.length > 0 && <div className="dropdown-divider" />}
                {customThemes.map((t) => {
                  const id = `custom:${t.id}`;
                  return (
                    <button
                      key={id}
                      className={`dropdown-item ${theme === id ? "active" : ""}`}
                      onClick={() => { setTheme(id); setShowThemeMenu(false); }}
                    >
                      <span>{t.emoji || "🎨"}</span> {t.name}
                    </button>
                  );
                })}
                <div className="dropdown-divider" />
                <button
                  className="dropdown-item dropdown-info"
                  onClick={() => { navigate("/themes"); setShowThemeMenu(false); }}
                >
                  <Palette size={10} /> Customize themes...
                </button>
              </div>
            )}
          </div>

          <SystemHealth />

          <NavLink to="/settings" className="topbar-action" title="Settings">
            <Settings size={14} />
          </NavLink>

          <button
            className="power-button"
            onClick={onOpenShutdown}
            title="Shutdown / cleanup"
          >
            <Power size={12} />
          </button>
        </div>
      </div>

    </>
  );
}
