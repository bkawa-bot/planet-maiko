import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ChevronDown, ChevronRight, MapPin, Search, Loader, Plug, AlertTriangle, BookOpen } from "lucide-react";
import ScheduledBriefings from "../components/ScheduledBriefings";
import ConceptsModal from "../components/ConceptsModal";
import IntegrationsSection from "../components/settings/IntegrationsSection";
import "./Settings.css";

export default function Settings() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pollerStatus, setPollerStatus] = useState({});
  const [message, setMessage] = useState("");

  const [openSections, setOpenSections] = useState({ overview: false, briefings: true, integrations: false, agents: false, routing: false, scene: false, plugins: false });
  const toggleSection = (key) => setOpenSections(s => ({ ...s, [key]: !s[key] }));

  const [showConcepts, setShowConcepts] = useState(false);
  const [locationQuery, setLocationQuery] = useState("");
  const [locationResolved, setLocationResolved] = useState("");
  const [lookingUp, setLookingUp] = useState(false);
  const [plugins, setPlugins] = useState([]);

  useEffect(() => {
    Promise.all([
      api.getConfig(),
      api.getPollerStatus(),
      api.getPlugins().catch(() => []),
    ]).then(([cfg, status, pluginList]) => {
      setConfig(cfg);
      setPollerStatus(status);
      setPlugins(pluginList);
      // Restore resolved location display from config
      if (cfg?.scene?.location_name && cfg?.scene?.latitude && cfg?.scene?.longitude) {
        setLocationResolved(
          `${cfg.scene.location_name} (${cfg.scene.latitude}, ${cfg.scene.longitude})`
        );
      }
      setLoading(false);
    }).catch((err) => {
      console.error("Failed to load settings:", err);
      setLoading(false);
    });
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateConfig(config);
      // Clear weather cache if location is set
      if (config?.scene?.latitude && config?.scene?.longitude) {
        await api.refreshScene().catch(() => {});
      }
      setMessage("Settings saved! Restart the server to apply poller changes.");
      setTimeout(() => setMessage(""), 5000);
    } catch (err) {
      setMessage("Failed to save: " + err.message);
    }
    setSaving(false);
  };

  const handleRunPoller = async (name) => {
    try {
      const result = await api.runPoller(name);
      setMessage(`${name} poller ran: ${result.created} new pupdate(s)`);
      setTimeout(() => setMessage(""), 5000);
    } catch (err) {
      setMessage(`Failed to run ${name}: ${err.message}`);
    }
  };

  const handleLocationLookup = async () => {
    if (!locationQuery.trim()) return;
    setLookingUp(true);
    try {
      const resp = await fetch(
        `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(locationQuery.trim())}&count=1&language=en&format=json`
      );
      const data = await resp.json();
      if (data.results && data.results.length > 0) {
        const r = data.results[0];
        const displayName = r.admin1 ? `${r.name}, ${r.admin1}` : r.name;
        setConfig((c) => ({
          ...c,
          scene: {
            ...c?.scene,
            latitude: r.latitude,
            longitude: r.longitude,
            location_name: displayName,
          },
        }));
        setLocationResolved(`${displayName} (${r.latitude}, ${r.longitude})`);
      } else {
        setLocationResolved("No results found");
      }
    } catch (err) {
      setLocationResolved("Lookup failed: " + err.message);
    }
    setLookingUp(false);
  };

  const updateField = (integration, field, value) => {
    setConfig((prev) => ({
      ...prev,
      [integration]: { ...prev[integration], [field]: value },
    }));
  };

  const updateRoleInstructions = (role, value) => {
    setConfig((prev) => ({
      ...prev,
      agents: {
        ...(prev.agents || {}),
        role_instructions: {
          ...(prev.agents?.role_instructions || {}),
          [role]: value,
        },
      },
    }));
  };

  if (loading) return <p className="settings-loading">Loading settings...</p>;
  if (!config) return <p className="settings-loading">Failed to load settings.</p>;

  return (
    <div className="settings-page">
      <h2>Settings</h2>
      {message && <div className="settings-message">{message}</div>}

      {/* Your Name */}
      <section className="settings-collapsible">
        <div className="collapsible-header" style={{ cursor: "default" }}>
          <span>Your Name</span>
          <button
            className="btn btn-sm"
            style={{ marginLeft: "auto", gap: 4 }}
            onClick={() => setShowConcepts(true)}
            title="Refresher on pupdates / tasks / agents / insights / learnings"
          >
            <BookOpen size={10} /> Concepts
          </button>
        </div>
        <div className="collapsible-body">
          <div className="integration-section">
            <div className="setup-hint">How Maiko addresses you in briefs and greetings.</div>
            <div className="integration-fields">
              <label>
                Name
                <input
                  type="text"
                  value={config.user?.name || ""}
                  onChange={(e) => setConfig((c) => ({ ...c, user: { ...(c.user || {}), name: e.target.value } }))}
                  placeholder="e.g. Brigitte"
                />
              </label>
            </div>
          </div>
        </div>
      </section>

      {showConcepts && <ConceptsModal onClose={() => setShowConcepts(false)} />}

      {/* Autopilot — master switch for auto-investigating incidents */}
      <section className="settings-collapsible">
        <div className="collapsible-header" style={{ cursor: "default" }}>
          <span>Autopilot</span>
        </div>
        <div className="collapsible-body">
          <div className="integration-section">
            <div className="setup-hint">
              When the correlator detects an incident (CI fail + deploy rollback,
              error spike chain, etc.), Maiko can auto-create an investigation
              task and kick off an investigation agent on it. Turn this off to
              require manual triage of every incident.
            </div>
            <div className="integration-fields">
              <label>
                <input
                  type="checkbox"
                  checked={config.brain?.auto_investigate?.enabled ?? true}
                  onChange={(e) => setConfig((c) => ({
                    ...c,
                    brain: {
                      ...(c.brain || {}),
                      auto_investigate: {
                        ...((c.brain && c.brain.auto_investigate) || {}),
                        enabled: e.target.checked,
                      },
                    },
                  }))}
                />
                Auto-investigate incidents
              </label>
              <label style={{ opacity: (config.brain?.auto_investigate?.enabled ?? true) ? 1 : 0.5 }}>
                <input
                  type="checkbox"
                  checked={config.brain?.auto_investigate?.dry_run ?? false}
                  disabled={!(config.brain?.auto_investigate?.enabled ?? true)}
                  onChange={(e) => setConfig((c) => ({
                    ...c,
                    brain: {
                      ...(c.brain || {}),
                      auto_investigate: {
                        ...((c.brain && c.brain.auto_investigate) || {}),
                        dry_run: e.target.checked,
                      },
                    },
                  }))}
                />
                Dry-run only (create the task so you can see what would've fired, skip the agent kickoff)
              </label>
              <label>
                Daily budget — hard stop after N auto-investigations per day
                <input
                  type="number"
                  min="1"
                  max="50"
                  value={config.brain?.auto_investigate?.daily_budget ?? 5}
                  onChange={(e) => setConfig((c) => ({
                    ...c,
                    brain: {
                      ...(c.brain || {}),
                      auto_investigate: {
                        ...((c.brain && c.brain.auto_investigate) || {}),
                        daily_budget: parseInt(e.target.value) || 5,
                      },
                    },
                  }))}
                />
              </label>
            </div>
          </div>
        </div>
      </section>

      {/* Home Overview — user-configurable add-on for the rolling LLM-generated overview pane */}
      <section className="settings-collapsible">
        <div className="collapsible-header" onClick={() => toggleSection("overview")}>
          {openSections.overview ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>Home Overview</span>
        </div>
        {openSections.overview && (
          <div className="collapsible-body">
            <div className="setup-hint">
              Maiko generates a warm daily overview for your Home page. You can give
              her an optional add-on instruction — anything you want her to also do
              when writing it. She has full tool access (Bash, WebFetch, your
              configured MCP servers), so she can actually go do these things.
            </div>
            <label style={{ display: "block", marginTop: 12 }}>
              <div style={{ marginBottom: 6, fontSize: 12, color: "var(--text-muted)" }}>
                Custom add-on instruction (optional)
              </div>
              <textarea
                style={{
                  width: "100%", minHeight: 100, padding: 8,
                  fontFamily: "inherit", fontSize: 13,
                  background: "var(--bg-card)", color: "var(--text)",
                  border: "1px solid var(--border)", borderRadius: "var(--radius-xs)",
                  resize: "vertical",
                }}
                value={config.overview?.custom_prompt || ""}
                onChange={(e) => updateField("overview", "custom_prompt", e.target.value)}
                placeholder={`e.g. "please also search my Slack for overnight mentions in #core-team" or "remind me which PRs have been sitting for more than 48 hours"`}
              />
            </label>
            <div className="setup-hint" style={{ marginTop: 8 }}>
              Overview regenerates roughly every 4 hours, or you can click Refresh
              on the pane itself. Changes here take effect on the next generation.
            </div>
          </div>
        )}
      </section>

      {/* Scheduled Briefings (morning brief, pack insights, etc.) */}
      <section className="settings-collapsible">
        <div className="collapsible-header" onClick={() => toggleSection("briefings")}>
          {openSections.briefings ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>Scheduled Briefings</span>
        </div>
        {openSections.briefings && (
          <div className="collapsible-body">
            <ScheduledBriefings />
          </div>
        )}
      </section>

      {/* NOTE on refactor pattern: big sections get their own file under
          components/settings/ (see IntegrationsSection below). Each
          takes the slice of config it needs plus shared helpers. Smaller
          sections (Your Name, Autopilot, Scene, Plugins) stay inline
          since extracting them would be more ceremony than payoff.
          Follow this pattern when adding new settings sections. */}
      <IntegrationsSection
        config={config}
        updateField={updateField}
        pollerStatus={pollerStatus}
        onRunPoller={handleRunPoller}
        onMessage={(m) => { setMessage(m); setTimeout(() => setMessage(""), 5000); }}
      />

      {/* INLINE-DEAD-START: old Integrations JSX kept commented-out
          temporarily to reduce the diff risk of the next extraction
          pass. Pure dead weight today — the parent `{false && (...)}`
          wrapper means React never renders it. Delete this block in a
          follow-up commit once the other sections are extracted and
          IntegrationsSection has soaked for a day. */}
      {false && (
        <section className="settings-collapsible">
        <div className="collapsible-header" onClick={() => toggleSection("integrations")}>
          {openSections.integrations ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>Integrations</span>
        </div>
        {openSections.integrations && (
          <div className="collapsible-body">
            <div className="integration-section">
              <h3>GitHub</h3>
              <div className="setup-hint">
                Requires the <code>gh</code> CLI to be installed and authenticated.
                Run <code>gh auth login</code> in your terminal first, then enter your username below.
              </div>
              <div className="integration-fields">
                <label>
                  <input
                    type="checkbox"
                    checked={config.github?.enabled || false}
                    onChange={(e) => updateField("github", "enabled", e.target.checked)}
                  />
                  Enabled
                </label>
                <label>
                  Username
                  <input
                    type="text"
                    value={config.github?.username || ""}
                    onChange={(e) => updateField("github", "username", e.target.value)}
                    placeholder="your-github-username"
                  />
                </label>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Repos</span>
                    <button
                      className="btn btn-sm"
                      disabled={discovering || !config.github?.username}
                      onClick={async () => {
                        setDiscovering(true);
                        try {
                          const result = await api.discoverGithubRepos();
                          if (result.repos?.length > 0) {
                            const existing = new Set(config.github?.repos || []);
                            const merged = [...existing, ...result.repos.filter(r => !existing.has(r))];
                            updateField("github", "repos", merged);
                            setMessage(`Found ${result.repos.length} repo(s)`);
                          } else {
                            setMessage("No repos found. Make sure gh CLI is authenticated.");
                          }
                        } catch (err) {
                          setMessage(err.message || "Discovery failed");
                        }
                        setDiscovering(false);
                        setTimeout(() => setMessage(""), 5000);
                      }}
                    >
                      {discovering ? <Loader size={10} className="spin" /> : <FolderGit2 size={10} />}
                      {discovering ? " Finding..." : " Auto-Discover"}
                    </button>
                  </div>
                  <div className="repo-list">
                    {(config.github?.repos || []).map((repo, i) => (
                      <div key={i} className="repo-list-item">
                        <span>{repo}</span>
                        <button className="btn-ghost" onClick={() => {
                          const updated = (config.github?.repos || []).filter((_, j) => j !== i);
                          updateField("github", "repos", updated);
                        }} title="Remove"><span style={{ fontSize: 14, color: "var(--urgent)" }}>&times;</span></button>
                      </div>
                    ))}
                  </div>
                  <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                    <input
                      type="text"
                      style={{ flex: 1 }}
                      placeholder="org/repo-name"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && e.target.value.trim()) {
                          const val = e.target.value.trim();
                          const repos = config.github?.repos || [];
                          if (!repos.includes(val)) updateField("github", "repos", [...repos, val]);
                          e.target.value = "";
                        }
                      }}
                    />
                    <span style={{ fontSize: 10, color: "var(--text-muted)", alignSelf: "center" }}>Press Enter to add</span>
                  </div>
                </div>
                <label>
                  Repository roots (local paths, comma-separated)
                  <input
                    type="text"
                    value={(config.github?.repo_roots || []).join(", ")}
                    onChange={(e) =>
                      updateField(
                        "github",
                        "repo_roots",
                        e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                      )
                    }
                    placeholder="~/src, ~/projects"
                  />
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    Where your repos live on disk. Used for agent worktrees.
                  </span>
                </label>
                <label>
                  Poll interval (minutes)
                  <input
                    type="number"
                    min="1"
                    value={config.github?.poll_interval_minutes || 5}
                    onChange={(e) =>
                      updateField("github", "poll_interval_minutes", parseInt(e.target.value) || 5)
                    }
                  />
                </label>
                {pollerStatus.github && (
                  <div className="poller-status">
                    Status: {pollerStatus.github.running ? "Running" : "Stopped"}
                    <button onClick={() => handleRunPoller("github")}>Run Now</button>
                  </div>
                )}
                <button className="btn btn-sm" onClick={async () => {
                  try {
                    const result = await api.testIntegration("github");
                    setMessage(result.status === "ok" ? `Connected as ${result.user}` : result.message);
                  } catch (err) { setMessage(err.message || "Test failed"); }
                  setTimeout(() => setMessage(""), 5000);
                }}>Test Connection</button>
              </div>
            </div>

            <div className="integration-section">
              <h3>Linear</h3>
              <div className="setup-hint">
                Get your API key from Linear: <strong>Settings → API → Personal API keys → Create key</strong>.
                Save the key, then pick your team below.
              </div>
              <div className="integration-fields">
                <label>
                  <input
                    type="checkbox"
                    checked={config.linear?.enabled || false}
                    onChange={(e) => updateField("linear", "enabled", e.target.checked)}
                  />
                  Enabled
                </label>
                <label>
                  API Key
                  <input
                    type="password"
                    value={config.linear?.api_key || ""}
                    onChange={(e) => updateField("linear", "api_key", e.target.value)}
                    placeholder="lin_api_..."
                  />
                </label>
                <label>
                  Team
                  {linearTeams.length > 0 ? (
                    <select
                      value={config.linear?.team_id || ""}
                      onChange={(e) => updateField("linear", "team_id", e.target.value)}
                    >
                      <option value="">— pick a team —</option>
                      {linearTeams.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}{t.key ? ` (${t.key})` : ""}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <div className="linear-team-picker-empty">
                      {config.linear?.team_id
                        ? <><code>{config.linear.team_id}</code> — click "Fetch my teams" to switch</>
                        : <>No team picked yet. Save your API key, then fetch teams.</>}
                    </div>
                  )}
                </label>
                <button className="btn btn-sm" onClick={async () => {
                  try {
                    const result = await api.getLinearTeams();
                    if (result?.teams?.length) {
                      setLinearTeams(result.teams);
                      setMessage(`Found ${result.teams.length} team${result.teams.length === 1 ? "" : "s"}`);
                    } else if (result?.error) {
                      setMessage(result.error);
                    } else {
                      setMessage("No teams found for this API key");
                    }
                  } catch (err) { setMessage(err.message || "Fetch failed"); }
                  setTimeout(() => setMessage(""), 5000);
                }}>Fetch my teams</button>
                {pollerStatus.linear && (
                  <div className="poller-status">
                    Status: {pollerStatus.linear.running ? "Running" : "Stopped"}
                    <button onClick={() => handleRunPoller("linear")}>Run Now</button>
                  </div>
                )}
                <button className="btn btn-sm" onClick={async () => {
                  try {
                    const result = await api.testIntegration("linear");
                    setMessage(result.status === "ok" ? `Connected as ${result.user}` : result.message);
                  } catch (err) { setMessage(err.message || "Test failed"); }
                  setTimeout(() => setMessage(""), 5000);
                }}>Test Connection</button>
              </div>
            </div>

            <div className="integration-section">
              <h3>Calendar</h3>
              <div className="setup-hint">
                Add your calendar's iCal/ICS URL. For Google Calendar: <strong>Settings → Calendar → Integrate calendar → Secret address in iCal format</strong>.
                For Outlook: <strong>Calendar settings → Shared calendars → Publish a calendar → ICS link</strong>.
              </div>
              <div className="integration-fields">
                <label>
                  <input
                    type="checkbox"
                    checked={config.calendar?.enabled || false}
                    onChange={(e) => updateField("calendar", "enabled", e.target.checked)}
                  />
                  Enabled
                </label>
                <label>
                  iCal URLs (one per line)
                  <textarea
                    value={(config.calendar?.ical_urls || []).join("\n")}
                    onChange={(e) =>
                      updateField(
                        "calendar",
                        "ical_urls",
                        e.target.value.split("\n").map((s) => s.trim()).filter(Boolean)
                      )
                    }
                    placeholder="https://calendar.google.com/..."
                    rows={3}
                  />
                </label>
                {pollerStatus.calendar && (
                  <div className="poller-status">
                    Status: {pollerStatus.calendar.running ? "Running" : "Stopped"}
                    <button onClick={() => handleRunPoller("calendar")}>Run Now</button>
                  </div>
                )}
              </div>
            </div>

          </div>
        )}
        </section>
      )}

      {/* Agent Preferences */}
      <section className="settings-collapsible">
        <div className="collapsible-header" onClick={() => toggleSection("agents")}>
          {openSections.agents ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>Agent Preferences</span>
        </div>
        {openSections.agents && (
          <div className="collapsible-body">
            <div className="integration-section">
              <div className="setup-hint">
                <strong>Role instructions</strong> apply team-wide to every agent of a given role.
                They get injected after the built-in role protocol and before each agent's own
                personality, so you can say "every reviewer cares about accessibility" once instead
                of editing every agent. Markdown is fine.
              </div>
              <div className="integration-fields">
                <label>
                  Coder instructions
                  <textarea
                    rows={4}
                    value={config.agents?.role_instructions?.coding || ""}
                    onChange={(e) => updateRoleInstructions("coding", e.target.value)}
                    placeholder={"e.g.\nAlways run tests before opening a PR.\nPrefer existing utilities in src/utils/ over adding new deps.\nNever commit TODO comments without an issue link."}
                    style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                  />
                </label>
                <label>
                  Reviewer instructions
                  <textarea
                    rows={4}
                    value={config.agents?.role_instructions?.review || ""}
                    onChange={(e) => updateRoleInstructions("review", e.target.value)}
                    placeholder={"e.g.\nAlways call out missing tests for new code paths.\nFlag any new dependency additions for discussion.\nCheck that error messages are user-facing-safe."}
                    style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                  />
                </label>
                <label>
                  Investigator instructions
                  <textarea
                    rows={4}
                    value={config.agents?.role_instructions?.investigation || ""}
                    onChange={(e) => updateRoleInstructions("investigation", e.target.value)}
                    placeholder={"e.g.\nCross-reference incidents with the on-call runbook.\nAlways propose a rollback path as the first mitigation.\nIf the stack trace crosses services, list each service involved."}
                    style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                  />
                </label>
              </div>

              <div className="setup-hint" style={{ marginTop: 16 }}>
                <strong>Legacy:</strong> the field below is the pre-roles global instruction string.
                Still honored — appended to every coding agent's CLAUDE.md alongside the role-specific
                block above. Safe to leave blank if you've moved to the per-role fields.
              </div>
              <div className="integration-fields">
                <label>
                  Global coding custom instructions (legacy)
                  <textarea
                    rows={3}
                    value={config.agents?.custom_instructions || ""}
                    onChange={(e) => updateField("agents", "custom_instructions", e.target.value)}
                    placeholder="e.g. Always write tests first. Use conventional commits."
                    style={{ fontFamily: "var(--font)", fontSize: 12 }}
                  />
                </label>
                <label>
                  Branch Prefix
                  <input
                    type="text"
                    value={config.agents?.branch_prefix || "maiko"}
                    onChange={(e) => updateField("agents", "branch_prefix", e.target.value)}
                    placeholder="maiko"
                  />
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    Auto-generated branches will be: prefix/task-title-slug
                  </span>
                </label>
                <label>
                  Allowed Tools (pre-approved for Claude Code sessions)
                  <div className="repo-list">
                    {(config.brain?.allowed_tools || []).map((tool, i) => (
                      <div key={i} className="repo-list-item">
                        <span>{tool}</span>
                        <button className="btn-ghost" onClick={() => {
                          const updated = (config.brain?.allowed_tools || []).filter((_, j) => j !== i);
                          setConfig((c) => ({ ...c, brain: { ...c?.brain, allowed_tools: updated } }));
                        }} title="Remove"><span style={{ fontSize: 14, color: "var(--urgent)" }}>&times;</span></button>
                      </div>
                    ))}
                  </div>
                  <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                    <input
                      type="text"
                      style={{ flex: 1 }}
                      placeholder="Tool name (e.g. Bash, Edit, mcp__github)"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && e.target.value.trim()) {
                          const val = e.target.value.trim();
                          const tools = config.brain?.allowed_tools || [];
                          if (!tools.includes(val)) setConfig((c) => ({ ...c, brain: { ...c?.brain, allowed_tools: [...tools, val] } }));
                          e.target.value = "";
                        }
                      }}
                    />
                    <span style={{ fontSize: 10, color: "var(--text-muted)", alignSelf: "center" }}>Press Enter to add</span>
                  </div>
                </label>
              </div>
            </div>
          </div>
        )}
      </section>

      {/* Model Routing */}
      <section className="settings-collapsible">
        <div className="collapsible-header" onClick={() => toggleSection("routing")}>
          {openSections.routing ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>Model Routing</span>
        </div>
        {openSections.routing && (
          <div className="collapsible-body">
            <div className="integration-section">
              <div className="setup-hint">
                Route tasks to different models to balance cost and quality.
                Haiku is cheapest for simple classifications, Sonnet is balanced for skills,
                Opus is best for coding and judging.
              </div>
              <div className="integration-fields">
                <label>
                  <input
                    type="checkbox"
                    checked={config.routing?.enabled ?? true}
                    onChange={(e) => updateField("routing", "enabled", e.target.checked)}
                  />
                  Enable cost-aware routing
                </label>
                <label>
                  Default model
                  <select value={config.routing?.default_model || "sonnet"} onChange={(e) => updateField("routing", "default_model", e.target.value)} className="routing-select">
                    <option value="haiku">Haiku</option>
                    <option value="sonnet">Sonnet</option>
                    <option value="opus">Opus</option>
                  </select>
                </label>
                <label title="Controls Claude's reasoning depth for every LLM call — triage, clustering, skill runs, AND the autonomous coding/review/investigation agents. Max burns more tokens but produces noticeably better agent output.">
                  Effort
                  <select value={config.routing?.thinking_budget || "medium"} onChange={(e) => updateField("routing", "thinking_budget", e.target.value)} className="routing-select">
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="max">Max</option>
                  </select>
                </label>
              </div>
              <div className="routing-rules-table">
                <div className="routing-rules-header">
                  <span>Task Type</span>
                  <span>Model</span>
                </div>
                {[
                  { key: "triage", label: "Triage (pupdate classification)", tier: "haiku" },
                  { key: "classify", label: "Signal classification", tier: "haiku" },
                  { key: "scene", label: "Scene creative note", tier: "haiku" },
                  { key: "conflict_query", label: "Conflict detection", tier: "haiku" },
                  { key: "skill", label: "Skills (default)", tier: "sonnet" },
                  { key: "skill:morning-brief", label: "Morning Brief", tier: "sonnet" },
                  { key: "skill:pr-review", label: "PR Review", tier: "sonnet" },
                  { key: "project_plan", label: "Project planning", tier: "sonnet" },
                  { key: "profile_judge", label: "Task outcome judging", tier: "sonnet" },
                  { key: "training:entry", label: "Training entries", tier: "opus" },
                  { key: "training:judge", label: "Training judging", tier: "opus" },
                  { key: "coding_agent", label: "Coding agents", tier: "opus" },
                ].map(({ key, label, tier }) => (
                  <div key={key} className="routing-rule-row">
                    <span className="routing-rule-label">{label}</span>
                    <select
                      className="routing-select"
                      value={(config.routing?.rules || {})[key] || tier}
                      onChange={(e) => {
                        const rules = { ...(config.routing?.rules || {}), [key]: e.target.value };
                        setConfig((c) => ({ ...c, routing: { ...c?.routing, rules } }));
                      }}
                    >
                      <option value="haiku">Haiku</option>
                      <option value="sonnet">Sonnet</option>
                      <option value="opus">Opus</option>
                    </select>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </section>

      {/* Scene & Weather (open by default) */}
      <section className="settings-collapsible">
        <div className="collapsible-header" onClick={() => toggleSection("scene")}>
          {openSections.scene ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>Scene & Weather</span>
        </div>
        {openSections.scene && (
          <div className="collapsible-body">
            <p className="integration-note">
              Enter your location to enable real weather data on the homepage. Uses the free Open-Meteo API (no key needed).
            </p>
            <div className="location-lookup">
              <label>
                <MapPin size={12} /> Location
              </label>
              <div className="location-lookup-row">
                <input
                  type="text"
                  value={locationQuery}
                  onChange={(e) => setLocationQuery(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleLocationLookup(); }}
                  placeholder="Zipcode or city name (e.g. 02101 or Boston)"
                  className="location-input"
                />
                <button
                  className="btn-lookup"
                  onClick={handleLocationLookup}
                  disabled={lookingUp || !locationQuery.trim()}
                >
                  <Search size={12} /> {lookingUp ? "Looking up..." : "Lookup"}
                </button>
              </div>
              {locationResolved && (
                <div className="location-resolved">{locationResolved}</div>
              )}
            </div>

            <div className="integration-fields" style={{ marginTop: 16 }}>
              <label>
                <input
                  type="checkbox"
                  checked={config.scene?.show_weather_overlay !== false}
                  onChange={(e) =>
                    setConfig((c) => ({ ...c, scene: { ...(c.scene || {}), show_weather_overlay: e.target.checked } }))
                  }
                />
                Show weather overlay (clouds, rain, snow, stars)
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={config.scene?.show_hill_background !== false}
                  onChange={(e) =>
                    setConfig((c) => ({ ...c, scene: { ...(c.scene || {}), show_hill_background: e.target.checked } }))
                  }
                />
                Show hill background
              </label>
            </div>
          </div>
        )}
      </section>

      {/* Plugins */}
      <section className="settings-collapsible">
        <div className="collapsible-header" onClick={() => toggleSection("plugins")}>
          {openSections.plugins ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>Plugins</span>
          {plugins.length > 0 && <span className="section-count">{plugins.length}</span>}
        </div>
        {openSections.plugins && (
          <div className="collapsible-body">
            {plugins.length === 0 ? (
              <p className="integration-note">
                No plugins detected. Drop <code>.py</code> files into <code>~/.maiko/plugins/</code> or install a pip package
                with the <code>planet_maiko.plugins</code> entry point.
              </p>
            ) : (
              <div className="plugins-list">
                {plugins.map((p) => (
                  <div key={p.name} className={`plugin-card ${p.status}`}>
                    <div className="plugin-card-header">
                      <Plug size={14} className={`plugin-icon status-${p.status}`} />
                      <div className="plugin-info">
                        <span className="plugin-name">{p.name}</span>
                        <span className="plugin-source">
                          {p.source === "local" ? p.file : `entry_point: ${p.entry_point}`}
                        </span>
                      </div>
                      <div className="plugin-status-area">
                        <span className={`badge ${p.status === "loaded" ? "active" : p.status === "disabled" ? "cancelled" : p.status === "pending_restart" ? "in_progress" : "urgent"}`}>
                          {p.status === "pending_restart" ? "restart needed" : p.status}
                        </span>
                        <label className="plugin-toggle">
                          <input
                            type="checkbox"
                            checked={p.status !== "disabled"}
                            onChange={async () => {
                              try {
                                const result = await api.togglePlugin(p.name);
                                setMessage(`Plugin "${p.name}" ${result.status}. Restart the server to apply.`);
                                setTimeout(() => setMessage(""), 8000);
                                // Refresh plugin list
                                const updated = await api.getPlugins();
                                setPlugins(updated);
                              } catch (err) {
                                setMessage("Failed to toggle plugin: " + err.message);
                              }
                            }}
                          />
                          <span className="toggle-slider" />
                        </label>
                      </div>
                    </div>
                    {p.status === "error" && p.error && (
                      <div className="plugin-error">
                        <AlertTriangle size={10} /> {p.error.split("\n").pop() || p.error}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <button className="btn-save" onClick={handleSave} disabled={saving}>
        {saving ? "Saving..." : "Save Settings"}
      </button>
    </div>
  );
}
