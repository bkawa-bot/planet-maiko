import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader, FolderGit2, AlertCircle, CheckCircle2 } from "@icons";
import { api } from "../../api/client";
import { relativeTime } from "../../utils/dates";

/**
 * Integrations section of Settings.jsx.
 *
 * Owns the three integration subsections (GitHub, Linear, Calendar)
 * plus their one-off state (Linear teams, GH repo discovery spinner).
 * Extracted to keep Settings.jsx a readable composition — this is
 * the largest chunk of the old monolith.
 *
 * Props:
 *   config         — current config blob
 *   updateField    — (integration, field, value) => void
 *   setConfig      — raw setter for nested updates
 *   pollerStatus   — map of poller name → { running }
 *   onRunPoller    — (name) => Promise
 *   onMessage      — (string) => void for transient setting-saved toasts
 *   openByDefault  — render the section expanded
 */
export default function IntegrationsSection({
  config, updateField, pollerStatus, onRunPoller, onMessage, openByDefault = false,
}) {
  const [open, setOpen] = useState(openByDefault);
  const [discovering, setDiscovering] = useState(false);
  const [linearTeams, setLinearTeams] = useState([]);
  const [linearCycle, setLinearCycle] = useState(null);
  // Per-integration test-connection result shown inline next to the
  // button. The old path sent these through onMessage → top of the page,
  // off-screen when the user scrolled down to the pager row.
  // { [name]: {status: "ok"|"error"|"testing", text: string} }
  const [testResult, setTestResult] = useState({});

  // Fetch the configured team's metadata (mainly activeCycle) so the
  // Linear section can show which cycle the Send-to-Linear modal will
  // default to. Quiet failure — not all Linear teams use cycles.
  useEffect(() => {
    const teamId = config.linear?.team_id;
    const apiKey = config.linear?.api_key;
    if (!teamId || !apiKey || !open) {
      setLinearCycle(null);
      return;
    }
    api.getLinearTeamMeta(teamId)
      .then((m) => setLinearCycle(m?.activeCycle || null))
      .catch(() => setLinearCycle(null));
  }, [config.linear?.team_id, config.linear?.api_key, open]);

  const discoverRepos = async () => {
    setDiscovering(true);
    try {
      const result = await api.discoverGithubRepos();
      if (result.repos?.length > 0) {
        const existing = new Set(config.github?.repos || []);
        const merged = [...existing, ...result.repos.filter(r => !existing.has(r))];
        updateField("github", "repos", merged);
        onMessage(`Found ${result.repos.length} repo(s)`);
      } else {
        onMessage("No repos found. Make sure gh CLI is authenticated.");
      }
    } catch (err) {
      onMessage(err.message || "Discovery failed");
    }
    setDiscovering(false);
  };

  const fetchLinearTeams = async () => {
    try {
      const result = await api.getLinearTeams();
      if (result?.teams?.length) {
        setLinearTeams(result.teams);
        onMessage(`Found ${result.teams.length} team${result.teams.length === 1 ? "" : "s"}`);
      } else if (result?.error) {
        onMessage(result.error);
      } else {
        onMessage("No teams found for this API key");
      }
    } catch (err) {
      onMessage(err.message || "Fetch failed");
    }
  };

  const testIntegration = async (name) => {
    setTestResult((prev) => ({ ...prev, [name]: { status: "testing", text: "Testing…" } }));
    try {
      const result = await api.testIntegration(name);
      if (result.status === "ok") {
        setTestResult((prev) => ({
          ...prev,
          [name]: { status: "ok", text: `Connected as ${result.user}` },
        }));
      } else {
        setTestResult((prev) => ({
          ...prev,
          [name]: { status: "error", text: result.message || "Test failed" },
        }));
      }
    } catch (err) {
      setTestResult((prev) => ({
        ...prev,
        [name]: { status: "error", text: err.message || "Test failed" },
      }));
    }
  };

  // Builds the status strip shown below each integration's poll-
  // interval row. Handles three cases honestly:
  //   - Poller registered + running: show last-run relative time,
  //     any error, last created count.
  //   - Poller registered but disabled: show disabled state.
  //   - Poller NOT in the status map at all: the entry point wasn't
  //     discovered — usually means pip install hasn't been re-run
  //     after a version bump. Tell the user, don't silently omit.
  const renderPollerStatus = (name) => {
    const status = pollerStatus[name];
    if (!status) {
      return (
        <div className="poller-status poller-status-missing">
          <AlertCircle size={11} /> Poller not registered. Try restarting the server after{" "}
          <code>pip install -e .</code> so entry points pick up.
        </div>
      );
    }
    const errMsg = status.last_error;
    const lastRun = status.last_run_at;
    const lastCreated = status.last_created_count;
    return (
      <div className={`poller-status poller-status-${errMsg ? "error" : status.running ? "ok" : "stopped"}`}>
        <div className="poller-status-row">
          <span>
            {status.running ? "Running" : "Stopped"}
            {lastRun && <> · last ran {relativeTime(lastRun)}</>}
            {!errMsg && lastCreated > 0 && <> · {lastCreated} new last poll</>}
          </span>
          <button onClick={() => onRunPoller(name)}>Run Now</button>
        </div>
        {errMsg && (
          <div className="poller-status-error" title={errMsg}>
            <AlertCircle size={10} /> {errMsg}
          </div>
        )}
      </div>
    );
  };

  const renderTestResult = (name) => {
    const r = testResult[name];
    if (!r) return null;
    const Icon = r.status === "ok" ? CheckCircle2 : r.status === "testing" ? Loader : AlertCircle;
    return (
      <span className={`test-result test-result-${r.status}`}>
        <Icon size={11} className={r.status === "testing" ? "spin" : ""} /> {r.text}
      </span>
    );
  };

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>Integrations</span>
      </div>
      {open && (
        <div className="collapsible-body">
          {/* GitHub */}
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
                    onClick={discoverRepos}
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
              {renderPollerStatus("github")}
              <div className="test-connection-row">
                <button className="btn btn-sm" onClick={() => testIntegration("github")}>Test Connection</button>
                {renderTestResult("github")}
              </div>
            </div>
          </div>

          {/* Linear */}
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
              <button className="btn btn-sm" onClick={fetchLinearTeams}>Fetch my teams</button>
              {linearCycle && (
                <div className="linear-cycle-chip" title="The Send-to-Linear modal defaults new issues to this cycle.">
                  <strong>Active cycle:</strong> #{linearCycle.number}
                  {linearCycle.name ? ` · ${linearCycle.name}` : ""}
                  {linearCycle.startsAt && linearCycle.endsAt
                    ? ` · ${_formatShort(linearCycle.startsAt)}–${_formatShort(linearCycle.endsAt)}`
                    : ""}
                  {typeof linearCycle.progress === "number"
                    ? ` · ${Math.round(linearCycle.progress * 100)}% through`
                    : ""}
                </div>
              )}
              {renderPollerStatus("linear")}
              <div className="test-connection-row">
                <button className="btn btn-sm" onClick={() => testIntegration("linear")}>Test Connection</button>
                {renderTestResult("linear")}
              </div>
            </div>
          </div>

          {/* PagerDuty */}
          <div className="integration-section">
            <h3>PagerDuty</h3>
            <div className="setup-hint">
              Create an API token in PagerDuty: <strong>User icon → My Profile → User Settings → API Access → Create New API User Token</strong>.
              Maiko polls for incidents assigned to you in triggered / acknowledged state.
            </div>
            <div className="integration-fields">
              <label>
                <input
                  type="checkbox"
                  checked={config.pagerduty?.enabled || false}
                  onChange={(e) => updateField("pagerduty", "enabled", e.target.checked)}
                />
                Enabled
              </label>
              <label>
                API Token
                <input
                  type="password"
                  value={config.pagerduty?.api_token || ""}
                  onChange={(e) => updateField("pagerduty", "api_token", e.target.value)}
                  placeholder="u+..."
                />
              </label>
              <label>
                Poll interval (minutes)
                <input
                  type="number"
                  min="1"
                  value={config.pagerduty?.poll_interval_minutes || 10}
                  onChange={(e) =>
                    updateField("pagerduty", "poll_interval_minutes", parseInt(e.target.value) || 10)
                  }
                />
              </label>
              {renderPollerStatus("pagerduty")}
              <div className="test-connection-row">
                <button className="btn btn-sm" onClick={() => testIntegration("pagerduty")}>Test Connection</button>
                {renderTestResult("pagerduty")}
              </div>
            </div>
          </div>

          {/* Calendar */}
          <div className="integration-section">
            <h3>Calendar</h3>
            <div className="setup-hint">
              Add your calendar's iCal/ICS URL. For Google Calendar:{" "}
              <strong>Settings → Calendar → Integrate calendar → Secret address in iCal format</strong>.
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
              {renderPollerStatus("calendar")}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}


// Short "Apr 23" style for cycle date chips. Locale-aware.
function _formatShort(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}
