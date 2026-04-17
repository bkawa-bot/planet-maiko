import { NavLink, useNavigate } from "react-router-dom";
import { Home, Inbox, CheckSquare, Bot, Brain, Wand2, Zap, GraduationCap, Settings, Shield, HelpCircle, X, Palette, Power } from "lucide-react";
import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import { applyCustomTheme, clearCustomTheme, hydrateCachedCustomTheme } from "../utils/themes";
import SystemHealth from "./SystemHealth";
import "./Sidebar.css";
import "./ShutdownModal.css";

const NAV_ITEMS = [
  { to: "/", icon: Home, label: "Home", end: true },
  { to: "/inbox", icon: Inbox, label: "Inbox", badgeKey: "pupdates" },
  { to: "/tasks", icon: CheckSquare, label: "Tasks" },
  { to: "/agents", icon: Bot, label: "Agents" },
  { to: "/knowledge", icon: Brain, label: "Knowledge" },
  { to: "/automations", icon: Zap, label: "Automations" },
  { to: "/training", icon: GraduationCap, label: "Training" },
];

const THEMES = [
  { id: "dark", label: "Night", emoji: "🌙" },
  { id: "light", label: "Day", emoji: "☀️" },
  { id: "morning", label: "Morning", emoji: "🌅" },
  { id: "afternoon", label: "Afternoon", emoji: "🍦" },
  { id: "sunset", label: "Sunset", emoji: "🌇" },
  { id: "auto", label: "Auto", emoji: "🔄" },
];

function getAutoTheme() {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 17) return "afternoon";
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
  const [badges, setBadges] = useState({});
  const [focusState, setFocusState] = useState("available");
  const [showFocusMenu, setShowFocusMenu] = useState(false);
  const [showFocusInfo, setShowFocusInfo] = useState(false);
  const themeRef = useRef(null);
  const focusRef = useRef(null);

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
        const [pupdates, tasks, learnings] = await Promise.all([
          api.getPupdates(),
          api.getTasks({ status: "new" }),
          api.getLearnings({ status: "pending" }).catch(() => []),
        ]);
        setBadges({
          pupdates: pupdates.filter((p) => !p.read).length,
          tasks: tasks.length,
          learnings: learnings.length,
        });
        const foc = await api.getFocus().catch(() => null);
        if (foc) setFocusState(foc.current_state || "available");
      } catch (err) { /* ignore */ }
    };
    fetchBadges();
    // Badges hit /api/pupdates + /api/tasks + /api/learnings + /api/focus
    // on every tick. 30s is the right balance — visible enough that a
    // freshly-arrived pupdate shows up quickly, slow enough that we're
    // not hammering the backend from every page.
    const interval = setInterval(fetchBadges, 30000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const handleClick = (e) => {
      if (themeRef.current && !themeRef.current.contains(e.target)) setShowThemeMenu(false);
      if (focusRef.current && !focusRef.current.contains(e.target)) setShowFocusMenu(false);
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
            <img src="/icon.png" alt="Maiko" className="topbar-icon" />
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
          <div className="focus-wrapper" ref={focusRef}>
            <button
              className={`focus-pill-topbar ${focusState}`}
              onClick={() => setShowFocusMenu(!showFocusMenu)}
              title="Focus mode"
            >
              <Shield size={10} /> {focusState.replace("_", " ")}
            </button>
            {showFocusMenu && (
              <div className="topbar-dropdown">
                {["available", "soft_focus", "deep_focus", "away"].map((s) => (
                  <button
                    key={s}
                    className={`dropdown-item ${focusState === s ? "active" : ""}`}
                    onClick={async () => {
                      await api.setFocus(s);
                      setFocusState(s);
                      setShowFocusMenu(false);
                    }}
                  >
                    {s.replace("_", " ")}
                  </button>
                ))}
                <div className="dropdown-divider" />
                <button className="dropdown-item dropdown-info" onClick={() => { setShowFocusInfo(true); setShowFocusMenu(false); }}>
                  <HelpCircle size={10} /> What is this?
                </button>
              </div>
            )}
          </div>

          <div className="theme-wrapper" ref={themeRef}>
            <button className="topbar-action" onClick={() => setShowThemeMenu(!showThemeMenu)} title="Theme">
              {themeEmoji}
            </button>
            {showThemeMenu && (
              <div className="topbar-dropdown">
                {THEMES.map((t) => (
                  <button
                    key={t.id}
                    className={`dropdown-item ${theme === t.id ? "active" : ""}`}
                    onClick={() => { setTheme(t.id); setShowThemeMenu(false); }}
                  >
                    <span>{t.emoji}</span> {t.label}
                  </button>
                ))}
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

      {showFocusInfo && (
        <div className="modal-overlay" onClick={() => setShowFocusInfo(false)}>
          <div className="info-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Shield size={16} /> Focus Mode
              <span style={{ flex: 1 }} />
              <button className="btn btn-sm" onClick={() => setShowFocusInfo(false)} style={{ border: "none", padding: 4 }}><X size={14} /></button>
            </div>
            <div className="modal-body info-modal-body">
              <p>Focus mode controls which notifications reach you. Set it based on how deep in the zone you are.</p>
              <h4>States</h4>
              <ul>
                <li><strong>Available</strong> — all notifications come through (critical, urgent, high, normal, low).</li>
                <li><strong>Soft focus</strong> — only critical, urgent, and high priority. Low-priority items are held until you exit.</li>
                <li><strong>Deep focus</strong> — only critical and urgent. Everything else is held.</li>
                <li><strong>Away</strong> — same as deep focus. Signals to agents and teammates that you're not around.</li>
              </ul>
              <h4>Held notifications</h4>
              <p>Notifications that were held during focus mode don't disappear — they're collected and released as a digest when you switch back to available.</p>
              <h4>Auto-focus</h4>
              <p>If you have a calendar integration, Maiko can auto-set soft focus when a meeting starts and return to available when it ends.</p>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
