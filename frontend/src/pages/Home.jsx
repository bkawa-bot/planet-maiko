import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  Shield, CheckSquare, Inbox as InboxIcon, FolderOpen, Brain, Calendar,
  AlertCircle, Palette, Video, Sunrise, GitBranch, Clock,
  ExternalLink, ChevronRight, ChevronDown, Play, Pin, Bot,
  Sparkles, GraduationCap, Wand2, Rocket,
} from "lucide-react";
import "./Home.css";
import "./Tasks.css";

function relativeTime(timestamp) {
  const now = Date.now();
  const diff = now - new Date(timestamp).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
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
  const [expandedFocus, setExpandedFocus] = useState(null);

  const [homeConfig, setHomeConfig] = useState(null);
  const [expandedFocusTask, setExpandedFocusTask] = useState(null);

  // Setup wizard state
  const [setupStep, setSetupStep] = useState(0);
  const [setupUsername, setSetupUsername] = useState("");
  const [setupRepos, setSetupRepos] = useState([]);
  const [setupDiscovering, setSetupDiscovering] = useState(false);
  const [setupLocation, setSetupLocation] = useState("");
  const [setupLocationResolved, setSetupLocationResolved] = useState("");
  const [setupLatLon, setSetupLatLon] = useState(null);

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

      // Calendar events from pupdates
      setCalendarEvents(pupdates.filter((p) => p.source === "calendar").slice(0, 5));

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
    const interval = setInterval(fetchAll, 15000);
    return () => clearInterval(interval);
  }, []);

  const runMorningBrief = async () => {
    setBriefLoading(true);
    showToast("Morning brief is brewing... ☕", "normal");
    try {
      const [p, t] = await Promise.all([api.getPupdates(), api.getTasks()]);
      const result = await api.runSkill("morning-brief", {
        context: {
          pupdates: JSON.stringify(p.slice(0, 20)),
          tasks: JSON.stringify(t.slice(0, 20)),
          calendar: JSON.stringify(calendarEvents),
        },
      });
      console.log("Morning brief result:", result);
      if (result.success) {
        setMorningBrief(result.output);
        setShowBrief(true);
        showToast("Your morning brief is ready! 🌅", "normal");
        // Result is auto-saved to skill_results by the backend
      } else {
        console.error("Brief failed:", result.error);
        showToast(result.error || "Couldn't fetch the brief right now", "high");
      }
    } catch (err) {
      console.error("Brief exception:", err);
      showToast("Something went wrong: " + err.message, "high");
    }
    setBriefLoading(false);
  };

  const focusState = focus?.current_state || "available";

  const TOTAL_STEPS = 9;
  const isFirstRun = homeConfig && !homeConfig.setup_complete;

  const finishSetup = async () => {
    const config = {};
    if (setupUsername) config.github = { username: setupUsername, enabled: true, repos: setupRepos };
    if (setupLatLon) config.scene = { latitude: setupLatLon.lat, longitude: setupLatLon.lon, location_name: setupLocationResolved };
    config.setup_complete = true;
    await api.updateConfig(config);
    window.location.reload();
  };

  if (isFirstRun) {
    return (
      <div className="home">
        <div className="setup-wizard">
          {/* Progress dots */}
          <div className="setup-progress">
            {Array.from({ length: TOTAL_STEPS }).map((_, i) => (
              <div key={i} className={`setup-dot ${i === setupStep ? "active" : ""} ${i < setupStep ? "done" : ""}`} />
            ))}
          </div>

          {/* Step 0: Welcome */}
          {setupStep === 0 && (
            <div className="setup-step setup-step-centered">
              <img src="/icon.png" alt="Maiko" style={{ width: 72, borderRadius: 16, imageRendering: "pixelated" }} />
              <h1>Welcome to Planet Maiko</h1>
              <p className="setup-sub">Your personal engineering companion. Maiko monitors your PRs, triages notifications, and orchestrates coding agents that learn from your team.</p>
              <button className="btn btn-primary" onClick={() => setSetupStep(1)} style={{ marginTop: 16 }}>
                <Rocket size={14} /> Get Started
              </button>
            </div>
          )}

          {/* Step 1: GitHub */}
          {setupStep === 1 && (
            <div className="setup-step">
              <div className="setup-step-icon"><GitBranch size={28} /></div>
              <h3>Connect GitHub</h3>
              <p>Enter your GitHub username so Maiko can monitor your PRs and reviews. Requires <code>gh auth login</code> first.</p>
              <input type="text" value={setupUsername} onChange={(e) => setSetupUsername(e.target.value)} placeholder="your-github-username" />
              <div className="setup-actions">
                <button className="setup-skip" onClick={() => setSetupStep(3)}>Skip</button>
                <button className="btn btn-primary" onClick={() => setSetupStep(2)} disabled={!setupUsername}>Next</button>
              </div>
            </div>
          )}

          {/* Step 2: Repos */}
          {setupStep === 2 && (
            <div className="setup-step">
              <div className="setup-step-icon"><FolderOpen size={28} /></div>
              <h3>Your Repos</h3>
              <p>Which repos should Maiko watch? Auto-discover from your recent activity, or type them manually.</p>
              <button className="btn" style={{ marginBottom: 8 }} onClick={async () => {
                setSetupDiscovering(true);
                try {
                  await api.updateConfig({ github: { username: setupUsername, enabled: true } });
                  const result = await api.discoverGithubRepos();
                  if (result.repos?.length) {
                    setSetupRepos(result.repos);
                    setTimeout(() => setSetupStep(3), 800);
                  }
                } catch (e) {}
                setSetupDiscovering(false);
              }} disabled={setupDiscovering}>
                {setupDiscovering ? "Discovering..." : "Auto-Discover Repos"}
              </button>
              <input type="text" value={setupRepos.join(", ")} onChange={(e) => setSetupRepos(e.target.value.split(",").map(s => s.trim()).filter(Boolean))} placeholder="org/repo1, org/repo2" />
              {setupRepos.length > 0 && <div style={{ fontSize: 12, color: "var(--green)", marginTop: 4 }}>Found {setupRepos.length} repo(s)</div>}
              <div className="setup-actions">
                <button className="setup-skip" onClick={() => setSetupStep(1)}>Back</button>
                <button className="btn btn-primary" onClick={() => setSetupStep(3)}>Next</button>
              </div>
            </div>
          )}

          {/* Step 3: Location */}
          {setupStep === 3 && (
            <div className="setup-step">
              <div className="setup-step-icon"><Palette size={28} /></div>
              <h3>Your Location</h3>
              <p>For live weather on your dashboard. Clouds drift across the page when it's overcast, rain falls when it's stormy.</p>
              <div style={{ display: "flex", gap: 8 }}>
                <input type="text" value={setupLocation} onChange={(e) => setSetupLocation(e.target.value)} placeholder="Boston" style={{ flex: 1 }} />
                <button className="btn" onClick={async () => {
                  try {
                    const resp = await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(setupLocation)}&count=1&language=en&format=json`);
                    const data = await resp.json();
                    if (data.results?.length) {
                      const r = data.results[0];
                      setSetupLocationResolved(`${r.name}, ${r.admin1 || ""}`);
                      setSetupLatLon({ lat: r.latitude, lon: r.longitude });
                    }
                  } catch (e) {}
                }}>Lookup</button>
              </div>
              {setupLocationResolved && <div style={{ fontSize: 12, color: "var(--green)", marginTop: 4 }}>{setupLocationResolved}</div>}
              <div className="setup-actions">
                <button className="setup-skip" onClick={() => setSetupStep(4)}>Skip</button>
                <button className="btn btn-primary" onClick={() => setSetupStep(4)}>Next</button>
              </div>
            </div>
          )}

          {/* Step 4: Tour — Inbox */}
          {setupStep === 4 && (
            <div className="setup-step setup-step-centered">
              <div className="setup-step-icon tour-icon"><InboxIcon size={36} /></div>
              <h3>Your Inbox</h3>
              <p>All your notifications land here — PRs, calendar events, CI alerts, and whatever integrations you connect. Maiko triages them automatically.</p>
              <p className="setup-detail">Tabs filter by type. You can dismiss, create tasks, or have Maiko investigate with one click.</p>
              <div className="setup-actions">
                <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
                <button className="btn btn-primary" onClick={() => setSetupStep(5)}>Next</button>
              </div>
            </div>
          )}

          {/* Step 5: Tour — Focus Mode */}
          {setupStep === 5 && (
            <div className="setup-step setup-step-centered">
              <div className="setup-step-icon tour-icon"><Shield size={36} /></div>
              <h3>Focus Mode</h3>
              <p>Control which notifications reach you based on how deep in the zone you are. Find it in the top-right of the nav bar.</p>
              <ul className="setup-checklist">
                <li><strong>Available</strong> — everything comes through</li>
                <li><strong>Soft focus</strong> — only high priority and above</li>
                <li><strong>Deep focus</strong> — only critical and urgent</li>
                <li><strong>Away</strong> — minimal interruptions</li>
              </ul>
              <p className="setup-detail">Held notifications are collected and released as a digest when you switch back.</p>
              <div className="setup-actions">
                <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
                <button className="btn btn-primary" onClick={() => setSetupStep(6)}>Next</button>
              </div>
            </div>
          )}

          {/* Step 6: Tour — Agents */}
          {setupStep === 6 && (
            <div className="setup-step setup-step-centered">
              <div className="setup-step-icon tour-icon"><Bot size={36} /></div>
              <h3>Meet Your Agents</h3>
              <p>Agents are coding assistants that each carry a personalized set of learnings tuned for specific task types. They work in isolated git worktrees.</p>
              <p className="setup-detail">New agents ("pups") explore with random learnings. Through training, they specialize and rank up.</p>
              <div className="setup-actions">
                <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
                <button className="btn btn-primary" onClick={() => setSetupStep(7)}>Next</button>
              </div>
            </div>
          )}

          {/* Step 7: Tour — Knowledge + Training */}
          {setupStep === 7 && (
            <div className="setup-step setup-step-centered">
              <div className="setup-step-icon tour-icon"><Brain size={36} /></div>
              <h3>Knowledge + Training</h3>
              <p>Maiko learns coding patterns from your PR review comments. These get injected into agent briefs so they follow your team's conventions.</p>
              <p className="setup-detail">Use <strong>Knowledge &gt; Backfill from PRs</strong> to scan your history. Then <strong>Training</strong> to teach agents on real merged PRs.</p>
              <div className="setup-actions">
                <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
                <button className="btn btn-primary" onClick={() => setSetupStep(8)}>Next</button>
              </div>
            </div>
          )}

          {/* Step 8: Tour — Done */}
          {setupStep === 8 && (
            <div className="setup-step setup-step-centered">
              <div className="setup-step-icon tour-icon"><Sparkles size={36} /></div>
              <h3>You're All Set!</h3>
              <p>Here's what to do next:</p>
              <ul className="setup-checklist">
                <li><strong>Connect integrations</strong> — Go to Settings to add Linear, Calendar, or other services</li>
                <li><strong>Backfill knowledge</strong> — Go to Knowledge and click "Backfill from PRs"</li>
                <li><strong>Create an agent</strong> — Visit Agents and click "New Agent"</li>
                <li><strong>Train it</strong> — Go to Training, pick a merged PR, and run a session</li>
              </ul>
              <button className="btn btn-primary" onClick={finishSetup} style={{ marginTop: 16 }}>
                <Rocket size={14} /> Go to Dashboard
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="home">
      {/* Page-level weather overlay */}
      {scene?.context?.weather && scene.context.weather !== "clear" && (
        <div className="page-weather-overlay">
          {(scene.context.weather === "cloudy" || scene.context.weather === "rain") && (
            <>
              <img src="/cloud1.svg" className="page-cloud page-cloud-1" alt="" />
              <img src="/cloud2.svg" className="page-cloud page-cloud-2" alt="" />
              <img src="/cloud3.svg" className="page-cloud page-cloud-3" alt="" />
              <img src="/cloud1.svg" className="page-cloud page-cloud-4" alt="" />
              <img src="/cloud2.svg" className="page-cloud page-cloud-5" alt="" />
              <img src="/cloud3.svg" className="page-cloud page-cloud-6" alt="" />
              <img src="/cloud1.svg" className="page-cloud page-cloud-7" alt="" />
            </>
          )}
          {scene.context.weather === "rain" && (
            <div className="page-rain">
              {Array.from({ length: 60 }).map((_, i) => (
                <div key={i} className="page-raindrop" style={{ left: `${(i * 1.7) + Math.random()}%`, animationDelay: `${Math.random() * 1.2}s`, animationDuration: `${0.6 + Math.random() * 0.4}s` }} />
              ))}
            </div>
          )}
          {scene.context.weather === "snow" && (
            <div className="page-snow">
              {Array.from({ length: 40 }).map((_, i) => (
                <div key={i} className="page-snowflake" style={{ left: `${i * 2.5 + Math.random() * 1.5}%`, animationDelay: `${Math.random() * 5}s`, animationDuration: `${3 + Math.random() * 3}s` }} />
              ))}
            </div>
          )}
          {scene.context.weather === "fog" && (
            <div className="page-fog" />
          )}
        </div>
      )}
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
            </div>
            {schedule && schedule.blocks?.length > 0 ? (
              <div className="focus-tasks">
                {schedule.blocks[0].tasks.slice(0, 3).map((t) => {
                  const statusColor = {
                    new: "var(--text-muted)", in_progress: "#60a5fa", waiting: "#fbbf24",
                    review: "#a78bfa", done: "#4ade80", cancelled: "#6b7280",
                  }[t.status] || "var(--text-muted)";
                  const statusIcon = {
                    new: "\ud83d\udccb", in_progress: "\ud83d\udd27", waiting: "\u23f3",
                    review: "\ud83d\udc40", done: "\u2705", cancelled: "\u26d4",
                  }[t.status] || "\ud83d\udccb";
                  const isExpanded = expandedFocusTask === t.id;
                  return (
                    <div key={t.id} className={`task-card ${isExpanded ? "expanded" : ""}`} onClick={() => setExpandedFocusTask(isExpanded ? null : t.id)} style={{ cursor: "pointer" }}>
                      <div className="task-status-indicator" style={{ background: statusColor }} />
                      <div className="task-icon" style={{ borderColor: statusColor }}>
                        <span className="task-icon-emoji">{statusIcon}</span>
                      </div>
                      <div className="task-content">
                        <div className="task-top">
                          <span className="task-title">{t.title}</span>
                          {(t.extra?.pinned || t.metadata?.pinned) && <Pin size={10} style={{ color: "var(--pink)", flexShrink: 0 }} />}
                        </div>
                        <div className="task-meta">
                          <span className="task-status-label" style={{ color: statusColor }}>{t.status.replace("_", " ")}</span>
                          {t.project_id && <span className="tag tag-project">{t.project_id}</span>}
                          <span className="task-type-label">{t.type}</span>
                          {t.due_date && <span className="tag tag-due"><Clock size={9} /> {t.due_date}</span>}
                          {t.assigned_agent_id && <span className="tag"><Bot size={9} /> {t.assigned_agent_id.replace("agent-", "").slice(0, 12)}</span>}
                        </div>
                        {isExpanded && (
                          <div className="focus-task-expanded" onClick={(e) => e.stopPropagation()}>
                            {t.url && (
                              <a href={t.url} target="_blank" rel="noreferrer" className="focus-task-link">
                                <ExternalLink size={10} /> {t.url.replace(/^https?:\/\//, "").slice(0, 50)}
                              </a>
                            )}
                            <div className="focus-task-actions">
                              {t.status === "new" && (
                                <button className="btn btn-sm btn-approve" onClick={async () => { await api.startTask(t.id); showToast("Task started", "normal"); }}>
                                  <Play size={10} /> Start
                                </button>
                              )}
                              {(t.status === "new" || t.status === "in_progress") && (
                                <button className="btn btn-sm btn-create" onClick={async () => { await api.completeTask(t.id); showToast("Task done!", "normal"); }}>
                                  <CheckSquare size={10} /> Done
                                </button>
                              )}
                              <button className="btn btn-sm" onClick={() => navigate("/tasks")}>
                                Open in Tasks
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                      {isExpanded ? <ChevronDown size={14} className="task-chevron open" /> : <ChevronRight size={14} className="task-chevron" />}
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
              {homeConfig?.scene?.latitude ? (
                <>
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
                </>
              ) : (
                <div className="scene-weather-fallback" onClick={() => navigate('/settings')} style={{ cursor: "pointer" }}>
                  <span className="weather-fallback-text">Set your location for live weather</span>
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

function renderMarkdown(text) {
  return text
    .replace(/\u00e2\u20ac\u201d/g, '\u2014')  // fix mojibake em dash
    .replace(/\u00e2\u20ac\u201c/g, '\u2014')
    .replace(/\u00e2\u20ac\u2122/g, '\u2019')  // fix mojibake apostrophe
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .replace(/^\- (.+)$/gm, '<li>$1</li>')
    .replace(/^(\d+)\. (.+)$/gm, '<li>$2</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
    .replace(/\|(.+)\|/g, (match) => {
      const cells = match.split('|').filter(c => c.trim());
      if (cells.every(c => c.trim().match(/^[-:]+$/))) return '';
      return '<tr>' + cells.map(c => `<td>${c.trim()}</td>`).join('') + '</tr>';
    })
    .replace(/(<tr>.*<\/tr>\n?)+/g, '<table>$&</table>')
    .replace(/^---$/gm, '<hr>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/^(?!<[hultop])(.+)$/gm, '<p>$1</p>')
    .replace(/<p><\/p>/g, '');
}
