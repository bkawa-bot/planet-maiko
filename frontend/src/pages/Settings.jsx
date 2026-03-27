import { useEffect, useState } from "react";
import { api } from "../api/client";
import "./Settings.css";

export default function Settings() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pollerStatus, setPollerStatus] = useState({});
  const [brainStatus, setBrainStatus] = useState(null);
  const [brainRules, setBrainRules] = useState([]);
  const [message, setMessage] = useState("");

  useEffect(() => {
    Promise.all([
      api.getConfig(),
      api.getPollerStatus(),
      api.getBrainStatus(),
      api.getBrainRules(),
    ]).then(([cfg, status, brain, rules]) => {
      setConfig(cfg);
      setPollerStatus(status);
      setBrainStatus(brain);
      setBrainRules(rules);
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

  const handleRunBrain = async () => {
    try {
      const result = await api.runBrainCycle();
      const p = result.pupdates || {};
      setMessage(
        `Brain cycle complete: ${p.processed || 0} processed, ${p.tasks_created || 0} tasks created, ${p.dismissed || 0} dismissed`
      );
      const status = await api.getBrainStatus();
      setBrainStatus(status);
      setTimeout(() => setMessage(""), 5000);
    } catch (err) {
      setMessage(`Brain cycle failed: ${err.message}`);
    }
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

      <section className="integration-section brain-section">
        <h3>Brain</h3>
        <div className="brain-status">
          <div className="brain-stat">
            <span className="brain-label">Cycles run:</span>
            <span>{brainStatus?.cycle_count || 0}</span>
          </div>
          <div className="brain-stat">
            <span className="brain-label">Last cycle:</span>
            <span>
              {brainStatus?.last_cycle
                ? new Date(brainStatus.last_cycle).toLocaleString()
                : "Never"}
            </span>
          </div>
          <button className="btn-brain" onClick={handleRunBrain}>
            Run Brain Cycle
          </button>
        </div>
        {brainRules.length > 0 && (
          <div className="brain-rules">
            <h4>Active Rules ({brainRules.length})</h4>
            <ul className="rules-list">
              {brainRules.map((r, i) => (
                <li key={i}>
                  <span className="rule-name">{r.name}</span>
                  <span className="rule-action">{r.action}</span>
                  <span className="rule-desc">{r.description}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="integration-section">
        <h3>GitHub</h3>
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
            <input
              type="text"
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
        </div>
      </section>

      <section className="integration-section">
        <h3>Linear</h3>
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
        </div>
      </section>

      <section className="integration-section">
        <h3>Calendar</h3>
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
      </section>

      <section className="integration-section">
        <h3>Slack</h3>
        <div className="integration-fields">
          <label>
            <input
              type="checkbox"
              checked={config.slack?.enabled || false}
              onChange={(e) => updateField("slack", "enabled", e.target.checked)}
            />
            Enabled
          </label>
          <label>
            Bot Token
            <input
              type="password"
              value={config.slack?.token || ""}
              onChange={(e) => updateField("slack", "token", e.target.value)}
              placeholder="xoxb-..."
            />
          </label>
          <label>
            Channels (comma-separated)
            <input
              type="text"
              value={(config.slack?.channels || []).join(", ")}
              onChange={(e) =>
                updateField(
                  "slack",
                  "channels",
                  e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                )
              }
              placeholder="#general, #engineering"
            />
          </label>
        </div>
      </section>

      <button className="btn-save" onClick={handleSave} disabled={saving}>
        {saving ? "Saving..." : "Save Settings"}
      </button>
    </div>
  );
}
