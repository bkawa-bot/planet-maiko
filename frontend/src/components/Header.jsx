import { NavLink } from "react-router-dom";
import {
  Home, Inbox, CheckSquare, Users, Brain, Lightbulb,
  Bot, Flame, BookOpen, Wand2, Settings, Bell, Shield,
} from "lucide-react";
import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import "./Header.css";

const NAV_ITEMS = [
  { to: "/", icon: Home, label: "Home", end: true },
  { to: "/inbox", icon: Inbox, label: "Inbox", badgeKey: "pupdates" },
  { to: "/tasks", icon: CheckSquare, label: "Tasks", badgeKey: "tasks" },
  { to: "/team", icon: Users, label: "Team" },
  { to: "/brainstorm", icon: Brain, label: "Brainstorm" },
  { to: "/suggestions", icon: Lightbulb, label: "Suggestions" },
  { to: "/agents", icon: Bot, label: "Agents" },
  { to: "/gathering", icon: Flame, label: "Gathering" },
  { to: "/skills", icon: Wand2, label: "Skills" },
  { to: "/settings", icon: Settings, label: "Settings" },
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

export default function Header() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem("maiko-theme") || "dark"
  );
  const [showThemeMenu, setShowThemeMenu] = useState(false);
  const [badges, setBadges] = useState({});
  const [focus, setFocus] = useState(null);
  const themeRef = useRef(null);

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
    api.getFocus().then(setFocus).catch(() => {});
  }, []);

  useEffect(() => {
    const handleClick = (e) => {
      if (themeRef.current && !themeRef.current.contains(e.target)) {
        setShowThemeMenu(false);
      }
    };
    document.addEventListener("click", handleClick);
    return () => document.removeEventListener("click", handleClick);
  }, []);

  const resolvedTheme = theme === "auto" ? getAutoTheme() : theme;
  const themeEmoji = THEMES.find((t) => t.id === theme)?.emoji || "🌙";

  return (
    <header className="header" data-header-theme={resolvedTheme}>
      <a className="logo" href="/">
        <img src="/icon.png" alt="Maiko" className="logo-icon" />
        <div>
          <div className="logo-text">PLANET MAIKO</div>
          <div className="logo-sub">home of deep learning dogs</div>
        </div>
      </a>

      <nav className="nav">
        {NAV_ITEMS.map(({ to, icon: Icon, label, end, badgeKey }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
          >
            <Icon size={14} />
            <span className="nav-label">{label}</span>
            {badgeKey && badges[badgeKey] > 0 && (
              <span className="nav-badge">{badges[badgeKey]}</span>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="header-right">
        {focus && focus.current_state !== "available" && (
          <span className="focus-indicator">
            <Shield size={12} /> {focus.current_state.replace("_", " ")}
          </span>
        )}

        <div className="theme-wrapper" ref={themeRef}>
          <button
            className="theme-toggle"
            onClick={() => setShowThemeMenu(!showThemeMenu)}
            title="Change theme"
          >
            {themeEmoji}
          </button>
          {showThemeMenu && (
            <div className="theme-dropdown">
              {THEMES.map((t) => (
                <button
                  key={t.id}
                  className={`theme-option ${theme === t.id ? "active" : ""}`}
                  onClick={() => { setTheme(t.id); setShowThemeMenu(false); }}
                >
                  <span>{t.emoji}</span> {t.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
