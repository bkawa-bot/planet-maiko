import { useEffect, useState } from "react";
import { api } from "../api/client";
import SetupWizard from "../components/SetupWizard";
import OverviewPane from "../components/OverviewPane";
import PetMaikoWidget from "../components/PetMaikoWidget";
import { formatTime, formatClock } from "../utils/dates";
import { Brain, Calendar, Palette, Video } from "lucide-react";
import "./Home.css";

// Home polls sidebar data (scene, brain status, calendar, task stats).
// The OverviewPane fetches its own data from /api/home/overview, so
// this page stays cheap — no more monstrous fan-out on every poll.
const HOME_POLL_INTERVAL_MS = 60_000;

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
  const [scene, setScene] = useState(null);
  const [stats, setStats] = useState({ tasks_new: 0, tasks_ip: 0, projects: 0 });
  const [brainStatus, setBrainStatus] = useState(null);
  const [calendarEvents, setCalendarEvents] = useState([]);
  const [homeConfig, setHomeConfig] = useState(null);

  const fetchSidebar = async () => {
    try {
      const [sc, tasksNew, tasksIp, projects, brain, cfg, pupdates] = await Promise.all([
        api.getScene(),
        api.getTasks({ status: "new" }),
        api.getTasks({ status: "in_progress" }),
        api.getProjects({ status: "active" }),
        api.getBrainStatus().catch(() => null),
        api.getConfig().catch(() => null),
        api.getPupdates(),
      ]);
      setScene(sc);
      setHomeConfig(cfg);
      setStats({
        tasks_new: tasksNew.length,
        tasks_ip: tasksIp.length,
        projects: projects.length,
      });
      setBrainStatus(brain);
      setCalendarEvents(
        pupdates
          .filter((p) => p.source === "calendar")
          .sort((a, b) => (a.metadata?.start || "").localeCompare(b.metadata?.start || "")),
      );
    } catch (err) {
      console.error("Home sidebar fetch failed", err);
    }
  };

  useEffect(() => {
    fetchSidebar();
    const interval = setInterval(fetchSidebar, HOME_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  const isFirstRun = homeConfig && !homeConfig.setup_complete;
  if (isFirstRun) {
    return <SetupWizard onComplete={() => window.location.reload()} />;
  }

  return (
    <div className="home">
      <div className="home-grid">
        {/* Main column: the overview pane IS the home experience. */}
        <div className="home-main">
          <OverviewPane />
        </div>

        {/* Sidebar widgets: ambient context, not primary surface. */}
        <div className="home-sidebar">
          <PetMaikoWidget />

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
                <div className="scene-weather-fallback" style={{ cursor: "default" }}>
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
            </div>
          </div>

          <div className="home-widget">
            <div className="widget-header"><Brain size={12} /> Brain</div>
            <div className="widget-detail">
              <span>Cycles: {brainStatus?.cycle_count || 0}</span>
              <span>Last: {brainStatus?.last_cycle ? formatTime(brainStatus.last_cycle) : "Never"}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
