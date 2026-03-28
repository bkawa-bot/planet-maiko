import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Shield, CheckSquare, Inbox, FolderOpen, Brain, Calendar,
  AlertCircle, Palette, Video,
} from "lucide-react";
import "./Home.css";

export default function Home() {
  const [scene, setScene] = useState(null);
  const [stats, setStats] = useState({ pupdates: 0, unread: 0, tasks_new: 0, tasks_ip: 0, projects: 0 });
  const [focus, setFocus] = useState(null);
  const [brainStatus, setBrainStatus] = useState(null);
  const [recentPupdates, setRecentPupdates] = useState([]);
  const [schedule, setSchedule] = useState(null);
  const [calendarEvents, setCalendarEvents] = useState([]);

  const fetchAll = async () => {
    try {
      const [sc, pupdates, tasksNew, tasksIp, projects, foc, brain, sched] = await Promise.all([
        api.getScene(),
        api.getPupdates(),
        api.getTasks({ status: "new" }),
        api.getTasks({ status: "in_progress" }),
        api.getProjects({ status: "active" }),
        api.getFocus().catch(() => null),
        api.getBrainStatus().catch(() => null),
        api.getSchedule().catch(() => null),
      ]);
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

      // Calendar events from pupdates
      setCalendarEvents(pupdates.filter((p) => p.source === "calendar").slice(0, 5));
    } catch (err) {
      console.error("Failed to load home:", err);
    }
  };

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 15000);
    return () => clearInterval(interval);
  }, []);

  const focusState = focus?.current_state || "available";

  return (
    <div className="home">
      {/* Status Bar */}
      <div className="home-status-bar">
        <div className="status-left">
          <span className="status-dot online" />
          <span className="status-text">
            Brain: {brainStatus?.cycle_count || 0} cycles
            {brainStatus?.last_cycle && ` · Last: ${new Date(brainStatus.last_cycle).toLocaleTimeString()}`}
          </span>
        </div>
        <div className="status-right">
          <span className="status-stat"><Inbox size={12} /> {stats.unread} unread</span>
          <span className="status-sep">·</span>
          <span className="status-stat"><CheckSquare size={12} /> {stats.tasks_ip} active</span>
          <span className="status-sep">·</span>
          <span className="status-stat"><FolderOpen size={12} /> {stats.projects} projects</span>
          <span className={`focus-pill ${focusState}`}>
            <Shield size={12} /> {focusState.replace("_", " ")}
          </span>
        </div>
      </div>

      <div className="home-grid">
        {/* Main content */}
        <div className="home-main">
          {/* Focus Card */}
          <div className="home-card home-focus-card">
            <div className="home-card-header">
              <CheckSquare size={14} /> Focus
            </div>
            {schedule && schedule.blocks?.length > 0 ? (
              <div className="focus-tasks">
                {schedule.blocks[0].tasks.slice(0, 3).map((t) => (
                  <div key={t.id} className="focus-task">
                    <span className={`badge ${t.priority}`}>{t.priority}</span>
                    <span className="focus-task-title">{t.title}</span>
                    <span className="badge" style={{fontSize: 9}}>{t.status.replace("_", " ")}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="home-card-empty">
                <span style={{ fontSize: 32 }}>🐾</span>
                <div className="empty-title" style={{ fontSize: 14 }}>No tasks yet</div>
                <button className="btn btn-primary">Start Morning Brief</button>
              </div>
            )}
          </div>

          {/* Also Waiting */}
          <div className="home-card">
            <div className="home-card-header">
              <AlertCircle size={14} /> Also Waiting
              {recentPupdates.length > 0 && (
                <span className="home-card-time">{recentPupdates.length} more</span>
              )}
            </div>
            {recentPupdates.length > 0 ? (
              <div className="recent-list">
                {recentPupdates.map((p) => (
                  <div key={p.id} className={`recent-item ${p.read ? "read" : ""}`}>
                    <span className={`priority-dot-sm ${p.priority}`} />
                    <span className="recent-source">{p.source}</span>
                    <span className="recent-title">{p.title}</span>
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
                      {e.metadata?.start ? new Date(e.metadata.start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ""}
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
              {scene?.context?.weather && (
                <div className="scene-weather">
                  {scene.context.weather === "clear" ? "☀️" :
                   scene.context.weather === "rain" ? "🌧️" :
                   scene.context.weather === "snow" ? "🌨️" :
                   scene.context.weather === "cloudy" ? "☁️" :
                   scene.context.weather === "fog" ? "🌫️" : "🌤️"}
                  {" "}{scene.context.weather}
                  {scene.context.temperature_f && ` · ${scene.context.temperature_f}°F`}
                </div>
              )}
              {scene?.scene?.creative_note ? (
                <div className="scene-creative-note">"{scene.scene.creative_note}"</div>
              ) : scene?.scene?.mood && (
                <div className="scene-creative-note">
                  {scene.context?.season === "spring" ? "A peaceful spring day filled with vivid flowers on the field" :
                   scene.context?.season === "summer" ? "Warm sunlight blankets the hills as fireflies dance at dusk" :
                   scene.context?.season === "autumn" ? "Golden leaves drift quietly across the cooling hillside" :
                   scene.context?.season === "winter" ? "A crisp stillness hangs over the frost-kissed landscape" :
                   "The hills rest quietly under a gentle sky"}
                </div>
              )}
              <div className="scene-tags">
                {scene?.context?.season && <span className="scene-tag">{scene.context.season}</span>}
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
                <div className="widget-stat-value" style={{color: "var(--blue)"}}>{stats.tasks_ip}</div>
                <div className="widget-stat-label">In Progress</div>
              </div>
              <div className="widget-stat">
                <div className="widget-stat-value" style={{color: "var(--pink)"}}>{stats.tasks_new}</div>
                <div className="widget-stat-label">New</div>
              </div>
              <div className="widget-stat">
                <div className="widget-stat-value" style={{color: "var(--green)"}}>{stats.projects}</div>
                <div className="widget-stat-label">Projects</div>
              </div>
              <div className="widget-stat">
                <div className="widget-stat-value" style={{color: "var(--lemon)"}}>{stats.pupdates}</div>
                <div className="widget-stat-label">Pupdates</div>
              </div>
            </div>
          </div>

          {/* Brain widget */}
          <div className="home-widget">
            <div className="widget-header"><Brain size={12} /> Brain</div>
            <div className="widget-detail">
              <span>Cycles: {brainStatus?.cycle_count || 0}</span>
              <span>Last: {brainStatus?.last_cycle ? new Date(brainStatus.last_cycle).toLocaleTimeString() : "Never"}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
