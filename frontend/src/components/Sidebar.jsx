import { NavLink, useNavigate } from "react-router-dom";
import { Home, CheckSquare, Bot, Brain, Zap, Settings, Palette, Power, Leaf } from "lucide-react";
import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import { applyCustomTheme, clearCustomTheme, hydrateCachedCustomTheme } from "../utils/themes";
import SystemHealth from "./SystemHealth";
import "./Sidebar.css";
import "./ShutdownModal.css";

const NAV_ITEMS = [
  { to: "/", icon: Home, label: "Home", end: true },
  { to: "/tasks", icon: CheckSquare, label: "Tasks" },
  { to: "/agents", icon: Bot, label: "Pack" },
  { to: "/knowledge", icon: Brain, label: "Knowledge" },
  { to: "/automations", icon: Zap, label: "Automations" },
];

// Built-in theme palette. Each entry's `group` controls dropdown
// section dividers — "auto" sits up top on its own, then the four
// time-of-day groups in dawn → night order. Adding a new theme is a
// matter of adding a row here and a [data-theme="<id>"] block in
// index.css with the matching color tokens.
const THEMES = [
  { id: "auto", label: "Auto", emoji: "🔄", group: "auto" },
  // Night
  { id: "dark", label: "Cosmic Nighttime", emoji: "🌙", group: "night" },
  { id: "midnight", label: "Midnight Violet", emoji: "🪐", group: "night" },
  { id: "aurora", label: "Aurora", emoji: "🌌", group: "night" },
  { id: "slime-garden", label: "Slime Garden", emoji: "🍀", group: "night" },
  { id: "forest-night", label: "Forest Night", emoji: "🌲", group: "night" },
  // Twilight (sunset + warm dusk)
  { id: "sunset", label: "Civil Twilight", emoji: "🌇", group: "twilight" },
  { id: "golden-hour", label: "Golden Hour", emoji: "🍯", group: "twilight" },
  // Morning (dawn / pastel)
  { id: "morning", label: "Sunrise", emoji: "🌅", group: "morning" },
  { id: "dawn-mist", label: "Dawn Mist", emoji: "🌷", group: "morning" },
  { id: "cherry-blossom", label: "Cherry Blossom", emoji: "🌸", group: "morning" },
  // Day (bright)
  { id: "light", label: "Bright Daylight", emoji: "☀️", group: "day" },
  { id: "mint-meadow", label: "Mint Meadow", emoji: "🌿", group: "day" },
  { id: "coral-reef", label: "Coral Reef", emoji: "🐚", group: "day" },
  { id: "lavender-dream", label: "Lavender Dream", emoji: "💜", group: "day" },
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
  const today = new Date().getDay();
  const isActualWeekend = today === 0 || today === 6;  // Sat or Sun
  const [theme, setTheme] = useState(() => {
    const saved = localStorage.getItem("maiko-theme") || "dark";
    // Re-apply cached custom theme ASAP so the first paint uses the right
    // colors instead of flashing the default dark theme.
    hydrateCachedCustomTheme(saved);
    return saved;
  });
  const [customThemes, setCustomThemes] = useState([]);
  const [showThemeMenu, setShowThemeMenu] = useState(false);
  const [badges, setBadges] = useState({});
  const [weekendMode, setWeekendMode] = useState(false);
  const [weekendBusy, setWeekendBusy] = useState(false);
  const themeRef = useRef(null);

  // Hydrate weekend-mode from config so the topbar pill reflects
  // the persisted state rather than defaulting to off on every reload.
  useEffect(() => {
    let cancelled = false;
    api.getConfig().then((cfg) => {
      if (!cancelled) setWeekendMode(Boolean(cfg?.user?.weekend_mode));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const toggleWeekendMode = async () => {
    if (weekendBusy) return;
    setWeekendBusy(true);
    const next = !weekendMode;
    setWeekendMode(next);  // optimistic — revert on failure
    try {
      const cfg = await api.getConfig();
      await api.updateConfig({
        ...cfg,
        user: { ...(cfg.user || {}), weekend_mode: next },
      });
    } catch {
      setWeekendMode(!next);  // revert
    }
    setWeekendBusy(false);
  };

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
    const fetchBadges = async () => {
      try {
        const [tasks, learnings] = await Promise.all([
          api.getTasks({ status: "new" }),
          api.getLearnings({ status: "pending" }).catch(() => []),
        ]);
        setBadges({
          tasks: tasks.length,
          learnings: learnings.length,
        });
      } catch (err) { /* ignore */ }
    };
    fetchBadges();
    // 30s poll — fresh enough that newly-arrived items show up quickly,
    // slow enough that we're not hammering the backend from every page.
    const interval = setInterval(fetchBadges, 30000);
    return () => clearInterval(interval);
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
            {NAV_ITEMS.map(({ to, icon: Icon, label, end, badgeKey }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) => `nav-pill ${isActive ? "active" : ""}`}
              >
                <Icon size={15} className="nav-pill-icon" />
                <span className="nav-pill-label">{label}</span>
                {badgeKey && badges[badgeKey] > 0 && (
                  <span className="nav-pill-badge">{badges[badgeKey]}</span>
                )}
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="topbar-right">
          {/* Weekend pill only shows on actual weekend days or when
              weekend_mode is already on. Reduces topbar noise during
              the week when the toggle is irrelevant, while still letting
              the user flip it off on a Monday morning. */}
          {(isActualWeekend || weekendMode) && (
            <button
              className={`weekend-pill-topbar ${weekendMode ? "on" : ""}`}
              onClick={toggleWeekendMode}
              disabled={weekendBusy}
              title={weekendMode ? "Weekend mode on. Click to resume." : "Weekend mode off. Click to go off-duty."}
            >
              <Leaf size={10} /> {weekendMode ? "weekend on" : "weekend"}
            </button>
          )}

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
