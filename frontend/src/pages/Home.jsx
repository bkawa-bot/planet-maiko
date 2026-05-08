import { useEffect, useState } from "react";
import { api } from "../api/client";
import SetupWizard from "../components/SetupWizard";
import OverviewPane from "../components/OverviewPane";
import MemosPane from "../components/MemosPane";
import PackAskBox from "../components/PackAskBox";
import CardAvatar from "../components/CardAvatar";
import { formatTime, formatClock } from "../utils/dates";
import { Brain, Calendar, Palette, Video, Sparkles, Users } from "lucide-react";
import { showToast } from "../components/Toast";
import FooterPendingPopover from "../components/FooterPendingPopover";
import { useNavigate } from "react-router-dom";
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
  // Currently-running AgentJobs. Lighter signal than "shipped today"
  // (which surfaced 24h-old completions) — the widget is meant to
  // answer "what's the pack actively chewing on right now" at a
  // glance.
  const [workingJobs, setWorkingJobs] = useState([]);
  const [brainStatus, setBrainStatus] = useState(null);
  const [calendarEvents, setCalendarEvents] = useState([]);
  const [homeConfig, setHomeConfig] = useState(null);
  const [cycling, setCycling] = useState(false);
  const [showPendingPopover, setShowPendingPopover] = useState(false);
  const navigate = useNavigate();

  const fetchSidebar = async () => {
    try {
      // Use /agents/activity (richer, includes last_message + status
      // pill + linked agent profile) instead of raw /agent-jobs. The
      // overview-generation flow now wakes every running agent for a
      // fresh status before regenerating, so last_message reflects
      // recent agent voice rather than initial heartbeat.
      const [sc, activity, brain, cfg, pupdates] = await Promise.all([
        api.getScene(),
        api.getAgentActivity().catch(() => []),
        api.getBrainStatus().catch(() => null),
        api.getConfig().catch(() => null),
        api.getPupdates(),
      ]);
      setScene(sc);
      setHomeConfig(cfg);
      setWorkingJobs(Array.isArray(activity) ? activity : []);
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
              <Calendar size={12} /> Today
              {calendarEvents.length > 0 && (
                <span className="widget-count">{calendarEvents.length} meeting(s)</span>
              )}
            </div>
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

          <div className="home-widget pack-square-widget">
            <div className="widget-header">
              <Users size={12} /> Pack
              {workingJobs.length > 0 && (
                <span className="widget-count">{workingJobs.length}</span>
              )}
            </div>
            {workingJobs.length > 0 ? (
              <div className="pack-square">
                {workingJobs.map((a) => {
                  // task_id post-unification is the AgentJob.id, so
                  // either field gets us to the unified job page.
                  const id = a.job_id || a.task_id;
                  const route = `/jobs/${id}`;
                  const name = a.agent_name || "agent";
                  const status = a.status || "active";
                  // CardAvatar resolves agent.avatar -> sprite path; the
                  // backend now drops agent_avatar onto each activity
                  // entry so we don't need a separate profile fetch.
                  const agentForAvatar = {
                    avatar: a.agent_avatar,
                    display_name: name,
                  };
                  return (
                    <button
                      key={id}
                      type="button"
                      className={`pack-square-villager pack-status-${status}`}
                      onClick={() => navigate(route)}
                      title={`${name} — ${a.task_title || a.task_type || "working"}`}
                    >
                      <span className="pack-square-avatar-wrap">
                        <CardAvatar agent={agentForAvatar} size={40} />
                        <span
                          className={`pack-square-state-dot state-${status}`}
                          aria-label={`status: ${status}`}
                        />
                      </span>
                      <span className="pack-square-bubble" role="tooltip">
                        <span className="pack-square-bubble-name">{name}</span>
                        {a.task_title && (
                          <span className="pack-square-bubble-title">{a.task_title}</span>
                        )}
                        {a.last_message && (
                          <span className="pack-square-bubble-msg">"{a.last_message}"</span>
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="widget-empty">The town square is quiet — nobody's out yet.</div>
            )}
          </div>

          <div className="home-widget">
            <div className="widget-header">
              <Sparkles size={12} /> Ask the pack
            </div>
            <PackAskBox />
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
                <Sparkles size={10} className={cycling ? "spin" : ""} />
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
