import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Shield, RefreshCw, CheckSquare, Inbox, FolderOpen, Bot, Brain, Calendar } from "lucide-react";
import "./Home.css";

export default function Home() {
  const [scene, setScene] = useState(null);
  const [stats, setStats] = useState({ pupdates: 0, unread: 0, tasks_new: 0, tasks_ip: 0, projects: 0 });
  const [focus, setFocus] = useState(null);
  const [brainStatus, setBrainStatus] = useState(null);
  const [recentPupdates, setRecentPupdates] = useState([]);
  const [schedule, setSchedule] = useState(null);

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
          <span className="status-stat"><CheckSquare size={12} /> {stats.tasks_ip} active</span>
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

          {/* Recent Pupdates */}
          <div className="home-card">
            <div className="home-card-header">
              <Inbox size={14} /> Recent Pupdates
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
              <div className="focus-empty">No pupdates. All clear!</div>
            )}
          </div>
        </div>

        {/* Sidebar widgets */}
        <div className="home-sidebar">
          {/* Scene widget */}
          <div className="home-widget scene-widget">
            <div className="scene-display" data-sky={scene?.scene?.sky || "clear_day"}>
              <div className="scene-hills-mini">
                <div className="hill-mini hill-far-m" />
                <div className="hill-mini hill-mid-m" />
                <div className="hill-mini hill-near-m" />
              </div>
              <div className="maiko-mini">🐕</div>
            </div>
            <div className="scene-info">
              <span className="scene-mood">{scene?.scene?.mood || ""}</span>
              {scene?.context?.season && <span className="scene-tag">{scene.context.season}</span>}
              {scene?.scene?.maiko_outfit && <span className="scene-tag">{scene.scene.maiko_outfit}</span>}
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
