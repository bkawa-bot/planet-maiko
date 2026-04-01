import { NavLink } from "react-router-dom";
import { Settings, Bell } from "lucide-react";
import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import "./Sidebar.css";

const NAV_ITEMS = [
  { to: "/", emoji: "🏠", label: "Home", end: true },
  { to: "/inbox", emoji: "📫", label: "Inbox", badgeKey: "pupdates" },
  { to: "/tasks", emoji: "📋", label: "Tasks", badgeKey: "tasks" },
  { to: "/agents", emoji: "🐕", label: "Agents" },
  { to: "/team", emoji: "👥", label: "Team" },
  { to: "/brainstorm", emoji: "🔮", label: "Brain" },
  { to: "/suggestions", emoji: "💡", label: "Ideas" },
  { to: "/gathering", emoji: "🔥", label: "EOD" },
  { to: "/skills", emoji: "✨", label: "Skills" },
];

const THEMES = [
  { id: "dark", label: "Night", emoji: "🌙" },
  { id: "light", label: "Day", emoji: "☀️" },
  { id: "morning", label: "Morning", emoji: "🌅" },
  { id: "sunset", label: "Sunset", emoji: "🌇" },
  { id: "auto", label: "Auto", emoji: "🔄" },
];

function getAutoTheme() {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 17) return "light";
  if (hour >= 17 && hour < 20) return "sunset";
  return "dark";
}

export default function Sidebar() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem("maiko-theme") || "dark"
  );
  const [showThemeMenu, setShowThemeMenu] = useState(false);
  const [showNav, setShowNav] = useState(false);
  const [badges, setBadges] = useState({});
  const themeRef = useRef(null);
  const navRef = useRef(null);

  useEffect(() => {
    const resolved = theme === "auto" ? getAutoTheme() : theme;
    document.documentElement.setAttribute("data-theme", resolved);
    localStorage.setItem("maiko-theme", theme);
    if (theme === "auto") {
      const interval = setInterval(() => {
        document.documentElement.setAttribute("data-theme", getAutoTheme());
      }, 300000);
      return () => clearInterval(interval);
    }
  }, [theme]);

  useEffect(() => {
    const fetchBadges = async () => {
      try {
        const [pupdates, tasks] = await Promise.all([
          api.getPupdates(),
          api.getTasks({ status: "new" }),
        ]);
        setBadges({
          pupdates: pupdates.filter((p) => !p.read).length,
          tasks: tasks.length,
        });
      } catch (err) { /* ignore */ }
    };
    fetchBadges();
    const interval = setInterval(fetchBadges, 15000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const handleClick = (e) => {
      if (themeRef.current && !themeRef.current.contains(e.target)) setShowThemeMenu(false);
      if (navRef.current && !navRef.current.contains(e.target)) setShowNav(false);
    };
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, []);

  const themeEmoji = THEMES.find((t) => t.id === theme)?.emoji || "🌙";

  return (
    <>
      {/* Frosted top bar — brand + utilities */}
      <div className="topbar">
        <NavLink to="/" className="topbar-logo">
          <img src="/icon.png" alt="Maiko" className="topbar-icon" />
          <div className="topbar-logo-text">
            <span className="topbar-planet">PLANET</span>
            <span className="topbar-maiko">MAIKO</span>
          </div>
        </NavLink>

        <div className="topbar-right">
          <NavLink to="/inbox" className="topbar-action" title="Notifications">
            <Bell size={14} />
            {badges.pupdates > 0 && <span className="topbar-action-badge">{badges.pupdates}</span>}
          </NavLink>

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
              </div>
            )}
          </div>

          <NavLink to="/settings" className="topbar-action" title="Settings">
            <Settings size={14} />
          </NavLink>
        </div>
      </div>

      {/* Planet nav — bottom left */}
      <div className="planet-nav" ref={navRef}>
        {/* The planet button */}
        <button
          className={`planet-btn ${showNav ? "open" : ""}`}
          onClick={() => setShowNav(!showNav)}
          title="Navigate"
        >
          🪐
        </button>

        {/* Radial menu — items orbit out from the planet */}
        {showNav && (
          <div className="planet-orbit">
            {NAV_ITEMS.map((item, i) => {
              // Fan from bottom-right to top-left in a quarter circle
              const total = NAV_ITEMS.length;
              const angle = (Math.PI * 0.5) + (i / (total - 1)) * (Math.PI * 0.55);
              const radius = 100;
              const x = Math.cos(angle) * radius;
              const y = -Math.sin(angle) * radius;

              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => `orbit-item ${isActive ? "active" : ""}`}
                  onClick={() => setShowNav(false)}
                  title={item.label}
                  style={{
                    transform: `translate(${x}px, ${y}px)`,
                    animationDelay: `${i * 30}ms`,
                  }}
                >
                  <span className="orbit-emoji">{item.emoji}</span>
                  <span className="orbit-label">{item.label}</span>
                  {item.badgeKey && badges[item.badgeKey] > 0 && (
                    <span className="orbit-badge">{badges[item.badgeKey]}</span>
                  )}
                </NavLink>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
