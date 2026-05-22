import { useEffect, useState } from "react";
import { api } from "../api/client";
import SetupWizard from "../components/SetupWizard";
import OverviewPane from "../components/OverviewPane";
import MemosPane from "../components/MemosPane";
import { formatTime, formatClock, formatLongDate } from "../utils/dates";
import { Brain, Sun, Video, RadarSweep } from "@icons";
import { showToast } from "../components/Toast";
import FooterPendingPopover from "../components/FooterPendingPopover";
import "./Home.css";

// Home polls sidebar data (scene, brain status, calendar, task stats).
// The OverviewPane fetches its own data from /api/home/overview, so
// this page stays cheap — no more monstrous fan-out on every poll.
const HOME_POLL_INTERVAL_MS = 60_000;

function weatherEmoji(w) {
  if (w === "clear") return "☀️";
  if (w === "rain") return "🌧️";
  if (w === "snow") return "🌨️";
  if (w === "cloudy") return "☁️";
  if (w === "fog") return "🌫️";
  return "🌤️";
}

// scene.context.moon_phase values come from brain/creativity/scene.py
// (_moon_phase). Pure date math, so it's present regardless of whether
// the user configured scene coordinates (unlike weather).
const MOON = {
  new: ["🌑", "New moon"],
  waxing_crescent: ["🌒", "Waxing crescent"],
  first_quarter: ["🌓", "First quarter"],
  waxing_gibbous: ["🌔", "Waxing gibbous"],
  full: ["🌕", "Full moon"],
  waning_gibbous: ["🌖", "Waning gibbous"],
  last_quarter: ["🌗", "Last quarter"],
  waning_crescent: ["🌘", "Waning crescent"],
};

export default function Home() {
  const [scene, setScene] = useState(null);
  const [brainStatus, setBrainStatus] = useState(null);
  const [calendarEvents, setCalendarEvents] = useState([]);
  const [homeConfig, setHomeConfig] = useState(null);
  const [cycling, setCycling] = useState(false);
  const [showPendingPopover, setShowPendingPopover] = useState(false);

  const fetchSidebar = async () => {
    try {
      const [sc, brain, cfg, pupdates] = await Promise.all([
        api.getScene(),
        api.getBrainStatus().catch(() => null),
        api.getConfig().catch(() => null),
        api.getPupdates(),
      ]);
      setScene(sc);
      setHomeConfig(cfg);
      setBrainStatus(brain);
      // Today's events only. Calendar pupdates use a YYYY-MM-DD date in
      // their source_id, so yesterday's events linger in the DB as
      // read-but-not-dismissed rows and would otherwise leak into
      // today's Today widget. Filter by the start timestamp's local
      // date matching today's local date.
      const todayStr = new Date().toLocaleDateString("en-CA"); // yyyy-mm-dd
      setCalendarEvents(
        pupdates
          .filter((p) => p.source === "calendar")
          .filter((p) => {
            const start = p.metadata?.start;
            if (!start) return false;
            // Compare on local-date strings so a 10pm meeting doesn't
            // drop off the list just because UTC rolled to tomorrow.
            return new Date(start).toLocaleDateString("en-CA") === todayStr;
          })
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

  const triggerCycle = async (e) => {
    e.stopPropagation();
    if (cycling) return;
    setCycling(true);
    showToast("Brain cycle running...", "normal");
    try {
      await api.runBrainCycle();
      showToast("Brain cycle done", "normal");
      fetchSidebar();
    } catch (err) {
      showToast(err.message || "Cycle failed", "high");
    } finally {
      setCycling(false);
    }
  };

  const isFirstRun = homeConfig && !homeConfig.setup_complete;
  if (isFirstRun) {
    return <SetupWizard onComplete={() => window.location.reload()} />;
  }

  return (
    <div className="home">
      <div className="home-grid">
        {/* Main column: overview narrative + unified memos feed.
            Memos folds in what was separately ReviewQueue (waiting-on
            -you items) and Notifications (info-only asks). PackStatusPane
            was retired here — its agent-status content overlapped with
            the Agents page, the stuck/waiting rows are memos already,
            and the "Ask the pack" surface moved to the sidebar widget. */}
        <div className="home-main">
          <OverviewPane />
          <MemosPane />
        </div>

        {/* Sidebar widgets: ambient context, not primary surface. */}
        <div className="home-sidebar">
          <div className="home-widget">
            <div className="widget-header">
              <Sun size={12} /> Today
              {calendarEvents.length > 0 && (
                <span className="widget-count">{calendarEvents.length} meeting(s)</span>
              )}
            </div>
            <div className="today-meta">
              {formatLongDate(new Date())}
              {MOON[scene?.context?.moon_phase] && (
                <> · {MOON[scene.context.moon_phase][0]} {MOON[scene.context.moon_phase][1]}</>
              )}
            </div>
            {scene?.context?.weather && homeConfig?.scene?.latitude && (
              <div className="today-weather">
                {weatherEmoji(scene.context.weather)} {scene.context.weather}
                {scene.context.temperature_f != null && ` · ${scene.context.temperature_f}°F`}
              </div>
            )}
            {calendarEvents.length > 0 ? (
              <div className="calendar-list">
                {calendarEvents.map((e) => {
                  const startIso = e.metadata?.start;
                  const endIso = e.metadata?.end;
                  // "Past" means the meeting's end time has passed, or
                  // (if no end time) the start is already ≥30 min ago.
                  // Using end (not start) keeps the currently-in-progress
                  // meeting un-struck through until it's genuinely over.
                  const now = Date.now();
                  let isPast = false;
                  if (endIso) {
                    isPast = new Date(endIso).getTime() <= now;
                  } else if (startIso) {
                    isPast = new Date(startIso).getTime() + 30 * 60 * 1000 <= now;
                  }
                  return (
                    <div key={e.id} className={`calendar-event${isPast ? " calendar-event-past" : ""}`}>
                      <span className="calendar-time">
                        {formatClock(startIso)}
                      </span>
                      <span className="calendar-title">{e.title}</span>
                      {e.url && !isPast && (
                        <a href={e.url} target="_blank" rel="noreferrer" className="calendar-zoom">
                          <Video size={10} />
                        </a>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="widget-empty">No meetings today</div>
            )}
          </div>

          <div className="home-widget home-brain-widget">
            <div className="widget-header">
              <Brain size={12} /> Brain
              <button
                className="brain-widget-cycle-btn"
                onClick={triggerCycle}
                disabled={cycling}
                title="Run a brain cycle now (route tasks, process pupdates, etc.)"
              >
                <RadarSweep size={10} className={cycling ? "spin" : ""} />
                {cycling ? " running…" : " cycle now"}
              </button>
            </div>
            <div className="widget-detail">
              <span>Cycles: {brainStatus?.cycle_count || 0}</span>
              <span>Last: {brainStatus?.last_cycle ? formatTime(brainStatus.last_cycle) : "Never"}</span>
            </div>
            {brainStatus?.pending && Object.values(brainStatus.pending).some(v => v > 0) && (
              <div className="brain-widget-pending-row">
                <button
                  className="brain-widget-pending"
                  onClick={() => setShowPendingPopover((v) => !v)}
                  title="Click for a breakdown"
                >
                  {Object.values(brainStatus.pending).reduce((a, b) => a + b, 0)} pending
                </button>
                {showPendingPopover && (
                  <FooterPendingPopover
                    pending={brainStatus.pending || {}}
                    onClose={() => setShowPendingPopover(false)}
                  />
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
