import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import SetupWizard from "../components/SetupWizard";
import WeatherOverlay from "../components/WeatherOverlay";
import { renderMarkdown } from "../utils/markdown";
import { formatTime, formatClock, relativeTime } from "../utils/dates";
import {
  CheckSquare, Inbox as InboxIcon, Brain, Calendar,
  AlertCircle, Palette, Video, Sunrise, Clock,
  ExternalLink, ChevronRight, ChevronDown, Play, Pin,
  Sparkles, X,
} from "lucide-react";
import "./Home.css";
import "./Tasks.css";
import "./Inbox.css";

const HOME_POLL_INTERVAL_MS = 15000;

const STATUS_COLORS = {
  new: "var(--text-muted)", in_progress: "#60a5fa", waiting: "#fbbf24",
  review: "#a78bfa", done: "#4ade80", cancelled: "#6b7280",
};

const STATUS_ICONS = {
  new: Play, in_progress: CheckSquare, waiting: Clock,
  review: Calendar, done: CheckSquare, cancelled: InboxIcon,
};

const MOON_EMOJI = {
  new: "🌑", waxing_crescent: "🌒", first_quarter: "🌓",
  waxing_gibbous: "🌔", full: "🌕", waning_gibbous: "🌖",
  last_quarter: "🌗", waning_crescent: "🌘",
};

function weatherEmoji(w) {
  if (w === "clear") return "☀️";
  if (w === "rain") return "🌧️";
  if (w === "snow") return "🌨️";
  if (w === "cloudy") return "☁️";
  if (w === "fog") return "🌫️";
  return "🌤️";
}

function seasonPoem(season) {
  if (season === "spring") return "A peaceful spring day filled with vivid flowers on the field";
  if (season === "summer") return "Warm sunlight blankets the hills as fireflies dance at dusk";
  if (season === "autumn") return "Golden leaves drift quietly across the cooling hillside";
  if (season === "winter") return "A crisp stillness hangs over the frost-kissed landscape";
  return "The hills rest quietly under a gentle sky";
}

export default function Home() {
  const navigate = useNavigate();
  const [scene, setScene] = useState(null);
  const [stats, setStats] = useState({ pupdates: 0, unread: 0, tasks_new: 0, tasks_ip: 0, projects: 0 });
  const [focus, setFocus] = useState(null);
  const [brainStatus, setBrainStatus] = useState(null);
  const [recentPupdates, setRecentPupdates] = useState([]);
  const [schedule, setSchedule] = useState(null);
  const [calendarEvents, setCalendarEvents] = useState([]);
  const [morningBrief, setMorningBrief] = useState(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [showBrief, setShowBrief] = useState(false);
  const [homeConfig, setHomeConfig] = useState(null);
  const [expandedFocusTask, setExpandedFocusTask] = useState(null);
  const [regenOpen, setRegenOpen] = useState(false);
  const [regenInput, setRegenInput] = useState("");
  const [regenLoading, setRegenLoading] = useState(false);

  const fetchAll = async () => {
    try {
      const [sc, pupdates, tasksNew, tasksIp, projects, foc, brain, sched, cfg] = await Promise.all([
        api.getScene(),
        api.getPupdates(),
        api.getTasks({ status: "new" }),
        api.getTasks({ status: "in_progress" }),
        api.getProjects({ status: "active" }),
        api.getFocus().catch(() => null),
        api.getBrainStatus().catch(() => null),
        api.getSchedule().catch(() => null),
        api.getConfig().catch(() => null),
      ]);
      setHomeConfig(cfg);
      setScene(sc);
      setStats({
        pupdates: pupdates.length,
        unread: pupdates.filter((p) => !p.read).length,
        tasks_new: tasksNew.length,
        tasks_ip: tasksIp.length,
        projects: projects.length,
      });
      setFocus(foc);
      setBrainStatus(brain);
      setRecentPupdates(pupdates.slice(0, 5));
      setSchedule(sched);
      setCalendarEvents(
        pupdates
          .filter((p) => p.source === "calendar")
          .sort((a, b) => (a.metadata?.start || "").localeCompare(b.metadata?.start || ""))
      );

      // Load most recent morning brief — only if from today
      if (!morningBrief) {
        api.getSkillResults("morning-brief").then((results) => {
          if (results.length > 0) {
            const briefDate = new Date(results[0].created_at).toDateString();
            const today = new Date().toDateString();
            if (briefDate === today) setMorningBrief(results[0].content);
          }
        }).catch(() => {});
      }
    } catch (err) {
      console.error("Failed to load home:", err);
    }
  };

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, HOME_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  const regenerateFocus = async () => {
    const hint = regenInput.trim();
    if (!hint) return;
    setRegenLoading(true);
    showToast("Reordering your focus... 🐾", "normal");
    try {
      const result = await api.regenerateSchedule(hint);
      if (result?.blocks) {
        setSchedule(result);
        setRegenOpen(false);
        setRegenInput("");
        showToast("Focus reordered!", "normal");
      } else {
        showToast(result?.error || "Couldn't reorder", "high");
      }
    } catch (err) {
      showToast("Something went wrong: " + err.message, "high");
    }
    setRegenLoading(false);
  };

  const clearFocusOverride = async () => {
    try {
      const result = await api.clearScheduleOverride();
      if (result?.blocks !== undefined) setSchedule(result);
    } catch (err) {
      showToast("Couldn't clear hint: " + err.message, "high");
    }
  };

  const runMorningBrief = async () => {
    setBriefLoading(true);
    showToast("Morning brief is brewing... ☕", "normal");
    try {
      // Run the quick scanner first so stuck-PR / stale-task suggestions
      // show up in the brief context. Best-effort — never block on it.
      await api.runScan().catch(() => {});
      const [p, t] = await Promise.all([api.getPupdates(), api.getTasks()]);
      const result = await api.runSkill("morning-brief", {
        context: {
          pupdates: JSON.stringify(p.slice(0, 20)),
          tasks: JSON.stringify(t.slice(0, 20)),
          calendar: JSON.stringify(calendarEvents),
        },
      });
      if (result.success) {
        setMorningBrief(result.output);
        setShowBrief(true);
        showToast("Your morning brief is ready! 🌅", "normal");
      } else {
        showToast(result.error || "Couldn't fetch the brief right now", "high");
      }
    } catch (err) {
      showToast("Something went wrong: " + err.message, "high");
    }
    setBriefLoading(false);
  };

  const isFirstRun = homeConfig && !homeConfig.setup_complete;

  if (isFirstRun) {
    return <SetupWizard onComplete={() => window.location.reload()} />;
  }

  return (
    <div className="home">
      <WeatherOverlay scene={scene} enabled={homeConfig?.scene?.show_weather_overlay !== false} />

      <div className="home-grid">
        {/* Main content */}
        <div className="home-main">
          {/* Morning Brief button */}
          <button
            className={`btn ${morningBrief ? "" : "btn-primary"} morning-brief-btn`}
            onClick={morningBrief ? () => setShowBrief(true) : runMorningBrief}
            disabled={briefLoading}
          >
            <Sunrise size={12} /> {briefLoading ? "Brewing... ☕" : morningBrief ? "View Morning Brief" : "Start Morning Brief"}
          </button>

          {/* Focus Card */}
          <div className="home-card home-focus-card">
            <div className="home-card-header">
              <CheckSquare size={14} /> Focus
              <button
                className="btn btn-sm focus-regen-toggle"
                style={{ marginLeft: "auto" }}
                onClick={() => setRegenOpen((v) => !v)}
                title="Regenerate with a hint"
              >
                <Sparkles size={11} /> Regenerate
              </button>
            </div>
            {schedule?.override?.instructions && (
              <div className="focus-override-chip">
                <Sparkles size={10} />
                <span className="focus-override-text">Hint: {schedule.override.instructions}</span>
                <button className="btn-ghost" onClick={clearFocusOverride} title="Clear hint">
                  <X size={12} />
                </button>
              </div>
            )}
            {regenOpen && (
              <div className="focus-regen-row">
                <input
                  type="text"
                  value={regenInput}
                  onChange={(e) => setRegenInput(e.target.value)}
                  placeholder='e.g. "prioritize reliability work"'
                  autoFocus
                  disabled={regenLoading}
                  onKeyDown={(e) => { if (e.key === "Enter") regenerateFocus(); }}
                />
                <button
                  className="btn btn-primary btn-sm"
                  onClick={regenerateFocus}
                  disabled={regenLoading || !regenInput.trim()}
                >
                  {regenLoading ? "Thinking..." : "Apply"}
                </button>
              </div>
            )}
            {schedule && schedule.blocks?.length > 0 ? (
              <div className="focus-tasks">
                {schedule.blocks[0].tasks.slice(0, 3).map((t) => {
                  const statusColor = STATUS_COLORS[t.status] || "var(--text-muted)";
                  const FocusIcon = STATUS_ICONS[t.status] || CheckSquare;
                  const isExpanded = expandedFocusTask === t.id;
                  const isPinned = t.extra?.pinned || t.metadata?.pinned;
                  return (
                    <div
                      key={t.id}
                      className={`card pupdate-card ${t.priority || "normal"} ${isExpanded ? "expanded" : ""}`}
                      onClick={() => setExpandedFocusTask(isExpanded ? null : t.id)}
                      style={{ cursor: "pointer" }}
                    >
                      <div className="card-left-bar" style={{ background: statusColor }} />
                      <div
                        className="card-source-icon"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (t.status === "new") { api.startTask(t.id); showToast("Started", "normal"); }
                          else if (t.status === "in_progress") { api.completeTask(t.id); showToast("Done!", "normal"); }
                        }}
                        style={{ cursor: t.status === "new" || t.status === "in_progress" ? "pointer" : "default" }}
                      >
                        <FocusIcon size={14} />
                      </div>
                      <div className="card-content">
                        <div className="card-top">
                          <span className="card-source" style={{ color: statusColor }}>{t.status.replace("_", " ")}</span>
                          <span className="card-title">{t.title}</span>
                          {isPinned && <Pin size={10} style={{ color: "var(--pink)", flexShrink: 0 }} />}
                        </div>
                        <div className="card-meta">
                          {t.type && <span className="card-type">{t.type}</span>}
                          {t.due_date && <span className="card-time"><Clock size={9} /> {t.due_date}</span>}
                        </div>
                        {isExpanded && (
                          <div className="focus-task-expanded" onClick={(e) => e.stopPropagation()}>
                            {t.url && (
                              <a href={t.url} target="_blank" rel="noreferrer" className="focus-task-link">
                                <ExternalLink size={10} /> {t.url.replace(/^https?:\/\//, "").slice(0, 50)}
                              </a>
                            )}
                            <div className="focus-task-actions">
                              <button className="btn btn-sm" onClick={() => navigate("/tasks")}>
                                Open in Tasks
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                      {isExpanded
                        ? <ChevronDown size={14} className="task-chevron open" />
                        : <ChevronRight size={14} className="task-chevron" />}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="home-card-empty">
                <span style={{ fontSize: 32 }}>🐾</span>
                <div className="empty-title" style={{ fontSize: 14 }}>No tasks yet</div>
                <button className="btn btn-primary" onClick={runMorningBrief} disabled={briefLoading}>
                  {briefLoading ? "Fetching brief..." : "Start Morning Brief"}
                </button>
              </div>
            )}
          </div>

          {/* Also Waiting */}
          <div className="home-card">
            <div className="home-card-header">
              <AlertCircle size={14} /> Also Waiting
            </div>
            {recentPupdates.length > 0 ? (
              <div className="recent-list">
                {recentPupdates.map((p) => (
                  <div key={p.id} className={`recent-item ${p.read ? "read" : ""}`} onClick={() => navigate('/inbox')}>
                    <span className={`priority-dot-sm ${p.priority}`} />
                    <span className="recent-source">{p.source}</span>
                    <span className="recent-title">{p.title}</span>
                    <span className="recent-type-tag">{p.type?.replace(/_/g, " ")}</span>
                    <span className="recent-time">{relativeTime(p.timestamp)}</span>
                    {p.action_hint && <span className="recent-hint">{p.action_hint}</span>}
                  </div>
                ))}
              </div>
            ) : (
              <div className="focus-empty">Nothing waiting. All clear!</div>
            )}
          </div>
        </div>

        {/* Sidebar widgets */}
        <div className="home-sidebar">
          {/* Calendar widget */}
          <div className="home-widget">
            <div className="widget-header">
              <Calendar size={12} /> Today
              {calendarEvents.length > 0 && (
                <span className="widget-count">{calendarEvents.length} meeting(s)</span>
              )}
            </div>
            {calendarEvents.length > 0 ? (
              <div className="calendar-list">
                {calendarEvents.map((e) => (
                  <div key={e.id} className="calendar-event">
                    <span className="calendar-time">
                      {formatClock(e.metadata?.start)}
                    </span>
                    <span className="calendar-title">{e.title}</span>
                    {e.url && (
                      <a href={e.url} target="_blank" rel="noreferrer" className="calendar-zoom">
                        <Video size={10} />
                      </a>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="widget-empty">No meetings today</div>
            )}
          </div>

          {/* Scene widget */}
          <div className="home-widget scene-widget">
            <div className="widget-header"><Palette size={12} /> Scene</div>
            <div className="scene-info">
              {homeConfig?.scene?.latitude ? (
                scene?.context?.weather && (
                  <div className="scene-weather">
                    {weatherEmoji(scene.context.weather)} {scene.context.weather}
                    {scene.context.temperature_f && ` · ${scene.context.temperature_f}°F`}
                  </div>
                )
              ) : (
                <div className="scene-weather-fallback" onClick={() => navigate('/settings')} style={{ cursor: "pointer" }}>
                  <span className="weather-fallback-text">Set your location for live weather</span>
                </div>
              )}
              {scene?.scene?.creative_note ? (
                <div className="scene-creative-note">"{scene.scene.creative_note}"</div>
              ) : scene?.scene?.mood && (
                <div className="scene-creative-note">{seasonPoem(scene.context?.season)}</div>
              )}
              <div className="scene-tags">
                {scene?.context?.season && <span className="scene-tag">{scene.context.season}</span>}
                {scene?.context?.moon_phase && (
                  <span className="scene-tag">
                    {MOON_EMOJI[scene.context.moon_phase] || "🌙"} {scene.context.moon_phase.replace("_", " ")}
                  </span>
                )}
                {scene?.scene?.maiko_outfit && scene.scene.maiko_outfit !== "default" && (
                  <span className="scene-tag">maiko: {scene.scene.maiko_outfit}</span>
                )}
                {scene?.scene?.specials?.map((s) => (
                  <span key={s} className="scene-tag">{s}</span>
                ))}
              </div>
            </div>
          </div>

          {/* Task stats */}
          <div className="home-widget">
            <div className="widget-header">Tasks</div>
            <div className="widget-stat-grid">
              <div className="widget-stat">
                <div className="widget-stat-value" style={{ color: "var(--blue)" }}>{stats.tasks_ip}</div>
                <div className="widget-stat-label">In Progress</div>
              </div>
              <div className="widget-stat">
                <div className="widget-stat-value" style={{ color: "var(--pink)" }}>{stats.tasks_new}</div>
                <div className="widget-stat-label">New</div>
              </div>
              <div className="widget-stat">
                <div className="widget-stat-value" style={{ color: "var(--green)" }}>{stats.projects}</div>
                <div className="widget-stat-label">Projects</div>
              </div>
              <div className="widget-stat">
                <div className="widget-stat-value" style={{ color: "var(--lemon)" }}>{stats.pupdates}</div>
                <div className="widget-stat-label">Pupdates</div>
              </div>
            </div>
          </div>

          {/* Brain widget */}
          <div className="home-widget">
            <div className="widget-header"><Brain size={12} /> Brain</div>
            <div className="widget-detail">
              <span>Cycles: {brainStatus?.cycle_count || 0}</span>
              <span>Last: {brainStatus?.last_cycle ? formatTime(brainStatus.last_cycle) : "Never"}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Morning Brief Modal */}
      {showBrief && morningBrief && (
        <div className="modal-overlay" onClick={() => setShowBrief(false)}>
          <div className="brief-modal" onClick={(e) => e.stopPropagation()}>
            <div className="brief-modal-header">
              <Sunrise size={18} />
              <span>Good morning! 🐕</span>
              <button className="btn btn-sm" onClick={() => setShowBrief(false)} style={{ marginLeft: "auto" }}>Close</button>
            </div>
            <div className="brief-modal-body">
              <div className="brief-content" dangerouslySetInnerHTML={{ __html: renderMarkdown(morningBrief) }} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
