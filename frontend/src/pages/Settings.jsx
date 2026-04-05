import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ChevronDown, ChevronRight, MapPin, Search, Loader, FolderGit2 } from "lucide-react";
import "./Settings.css";

export default function Settings() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pollerStatus, setPollerStatus] = useState({});
  const [message, setMessage] = useState("");

  const [openSections, setOpenSections] = useState({ integrations: false, agents: false, routing: false, scene: true });
  const toggleSection = (key) => setOpenSections(s => ({ ...s, [key]: !s[key] }));

  const [locationQuery, setLocationQuery] = useState("");
  const [locationResolved, setLocationResolved] = useState("");
  const [lookingUp, setLookingUp] = useState(false);
  const [discovering, setDiscovering] = useState(false);

  useEffect(() => {
    Promise.all([
      api.getConfig(),
      api.getPollerStatus(),
    ]).then(([cfg, status]) => {
      setConfig(cfg);
      setPollerStatus(status);
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

  if (loading) return <p className="settings-loading">Loading settings...</p>;
  if (!config) return <p className="settings-loading">Failed to load settings.</p>;

  return (
    <div className="settings-page">
      <h2>Settings</h2>
      {message && <div className="settings-message">{message}</div>}

      {/* Integrations (collapsed by default) */}
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
                <label>
                  Repos (comma-separated)
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input
                      type="text"
                      style={{ flex: 1 }}
                      value={(config.github?.repos || []).join(", ")}
                      onChange={(e) =>
                        updateField(
                          "github",
                          "repos",
                          e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                        )
                      }
                      placeholder="org/repo1, org/repo2"
                    />
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
                            setMessage(`Found ${result.repos.length} repo(s) via ${result.source}`);
                          } else {
                            setMessage("No repos found. Make sure gh CLI is authenticated.");
                          }
                        } catch (err) {
                          setMessage(err.message || "Discovery failed");
                        }
                        setDiscovering(false);
                        setTimeout(() => setMessage(""), 5000);
                      }}
                      title="Auto-discover repos from your recent GitHub activity"
                    >
                      {discovering ? <Loader size={10} className="spin" /> : <FolderGit2 size={10} />}
                      {discovering ? " Finding..." : " Discover"}
                    </button>
                  </div>
                </label>
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
                Find your Team ID in the URL when viewing your team (e.g. <code>linear.app/team/<strong>TEAM-ID</strong>/...</code>).
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
                  Team ID
                  <input
                    type="text"
                    value={config.linear?.team_id || ""}
                    onChange={(e) => updateField("linear", "team_id", e.target.value)}
                  />
                </label>
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
                Custom instructions added to every agent's context. Use this for your workflow preferences,
                coding standards, or anything you want all agents to follow.
              </div>
              <div className="integration-fields">
                <label>
                  Custom Instructions
                  <textarea
                    rows={5}
                    value={config.agents?.custom_instructions || ""}
                    onChange={(e) => updateField("agents", "custom_instructions", e.target.value)}
                    placeholder="e.g. Always write tests first. Use conventional commits. Follow the error handling patterns in src/utils/errors.py."
                    style={{ fontFamily: "var(--font)", fontSize: 12 }}
                  />
                </label>
                <label>
                  Allowed Tools (pre-approved for Claude Code sessions)
                  <input
                    type="text"
                    value={(config.brain?.allowed_tools || []).join(", ")}
                    onChange={(e) => {
                      const tools = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
                      setConfig((c) => ({ ...c, brain: { ...c?.brain, allowed_tools: tools } }));
                    }}
                    placeholder="Bash, Read, Edit, Write, WebFetch, WebSearch, mcp__github"
                  />
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    Comma-separated. These tools won't require permission prompts when agents run.
                  </span>
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
                Route tasks to different model tiers to balance cost and quality.
                Haiku handles quick classifications, Sonnet runs skills and planning,
                Opus powers tournaments and coding agents.
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
                  <select
                    value={config.routing?.default_model || "sonnet"}
                    onChange={(e) => updateField("routing", "default_model", e.target.value)}
                    style={{
                      padding: "8px 12px",
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius-xs)",
                      background: "var(--bg)",
                      color: "var(--text)",
                      fontSize: "0.85rem",
                      fontFamily: "inherit",
                    }}
                  >
                    <option value="haiku">Haiku (fastest, cheapest)</option>
                    <option value="sonnet">Sonnet (balanced)</option>
                    <option value="opus">Opus (highest quality)</option>
                  </select>
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    Used for task types that don't have an explicit routing rule.
                  </span>
                </label>
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
          </div>
        )}
      </section>

      <button className="btn-save" onClick={handleSave} disabled={saving}>
        {saving ? "Saving..." : "Save Settings"}
      </button>
    </div>
  );
}
