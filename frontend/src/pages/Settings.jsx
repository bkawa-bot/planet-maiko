import { useEffect, useState, useRef } from "react";
import { api } from "../api/client";
import { BookOpen } from "@icons";
import ConceptsModal from "../components/ConceptsModal";
import AutopilotSection from "../components/settings/AutopilotSection";
import HomeOverviewSection from "../components/settings/HomeOverviewSection";
import RepoChecksSection from "../components/settings/RepoChecksSection";
import AgentPreferencesSection from "../components/settings/AgentPreferencesSection";
import ModelRoutingSection from "../components/settings/ModelRoutingSection";
import SceneSection from "../components/settings/SceneSection";
import PluginsSection from "../components/settings/PluginsSection";
import WorktreeMaintenanceSection from "../components/settings/WorktreeMaintenanceSection";
import { invalidateDefaultOrg } from "../utils/repo";
import "./Settings.css";

/**
 * Settings page — composition root for the section components under
 * components/settings/ (Autopilot, Home Overview, Repo Checks,
 * Integrations, Agent Preferences, Model Routing, Scene & Weather,
 * Plugins). Only the trivial "Your Name" stays inline.
 */
export default function Settings() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saveState, setSaveState] = useState("idle"); // idle | saving | saved | error
  const savedRef = useRef(null);   // last successfully-persisted config
  const saveTimer = useRef(null);  // debounce handle
  const savingRef = useRef(false); // a PUT /config is in flight
  const [pollerStatus, setPollerStatus] = useState({});
  const [message, setMessage] = useState("");
  const [showConcepts, setShowConcepts] = useState(false);
  const [plugins, setPlugins] = useState([]);

  // Persistent location-resolved hint, derived from saved config so a
  // returning user sees their previously-resolved name even before
  // they re-run the lookup.
  const [initialResolved, setInitialResolved] = useState("");

  useEffect(() => {
    Promise.all([
      api.getConfig(),
      api.getPollerStatus(),
      api.getPlugins().catch(() => []),
    ]).then(([cfg, status, pluginList]) => {
      setConfig(cfg);
      // Baseline so the autosave effect only fires on real edits.
      savedRef.current = JSON.parse(JSON.stringify(cfg || {}));
      setPollerStatus(status);
      setPlugins(pluginList);
      if (cfg?.scene?.location_name && cfg?.scene?.latitude && cfg?.scene?.longitude) {
        setInitialResolved(
          `${cfg.scene.location_name} (${cfg.scene.latitude}, ${cfg.scene.longitude})`,
        );
      }
      setLoading(false);
    }).catch((err) => {
      console.error("Failed to load settings:", err);
      setLoading(false);
    });
  }, []);

  // Debounced per-section autosave. PUT /config reloads from disk and
  // shallow-merges per top-level section (skipping redacted ***), so
  // sending only the sections that changed is safe and won't clobber
  // the rest or backend-written state. No manual Save button.
  useEffect(() => {
    if (!config || !savedRef.current) return;

    const changedKeys = Object.keys(config).filter(
      (k) => JSON.stringify(config[k]) !== JSON.stringify(savedRef.current[k]),
    );
    if (changedKeys.length === 0) return;

    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      if (savingRef.current) {
        // A save is in flight — re-poke so we retry after it lands,
        // rather than double-submitting.
        setConfig((c) => ({ ...c }));
        return;
      }
      const snapshot = JSON.parse(JSON.stringify(config));
      const patch = {};
      for (const k of changedKeys) patch[k] = snapshot[k];

      savingRef.current = true;
      setSaveState("saving");
      try {
        await api.updateConfig(patch);
        // Mark exactly the sent sections as saved, using the snapshot
        // we sent so edits made mid-request still count as unsaved.
        const base = savedRef.current
          ? JSON.parse(JSON.stringify(savedRef.current))
          : {};
        for (const k of changedKeys) base[k] = snapshot[k];
        savedRef.current = base;
        setSaveState("saved");
        if (changedKeys.includes("github")) invalidateDefaultOrg();
        if (
          changedKeys.includes("scene") &&
          snapshot.scene?.latitude &&
          snapshot.scene?.longitude
        ) {
          api.refreshScene().catch(() => {});
        }
      } catch (err) {
        setSaveState("error");
      } finally {
        savingRef.current = false;
      }
    }, 700);

    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [config]);

  const handleRunPoller = async (name) => {
    try {
      const result = await api.runPoller(name);
      flash(`${name} poller ran: ${result.created} new pupdate(s)`);
    } catch (err) {
      flash(`Failed to run ${name}: ${err.message}`);
    }
  };

  const flash = (m) => {
    setMessage(m);
    setTimeout(() => setMessage(""), 5000);
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

      {/* Your Name — small enough to stay inline. */}
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
                  placeholder="your first name"
                />
              </label>
            </div>
          </div>
        </div>
      </section>

      {showConcepts && <ConceptsModal onClose={() => setShowConcepts(false)} />}

      <AutopilotSection config={config} setConfig={setConfig} />
      <HomeOverviewSection config={config} updateField={updateField} />
      <RepoChecksSection />

      <AgentPreferencesSection
        config={config}
        setConfig={setConfig}
        updateField={updateField}
        updateRoleInstructions={updateRoleInstructions}
      />

      <WorktreeMaintenanceSection config={config} setConfig={setConfig} />

      <ModelRoutingSection
        config={config}
        setConfig={setConfig}
        updateField={updateField}
      />

      <SceneSection
        config={config}
        setConfig={setConfig}
        initialResolved={initialResolved}
      />

      <PluginsSection
        config={config}
        setConfig={setConfig}
        plugins={plugins}
        setPlugins={setPlugins}
        onMessage={flash}
        pollerStatus={pollerStatus}
        onRunPoller={handleRunPoller}
      />

      <div style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{
          fontSize: 13,
          color: saveState === "error" ? "var(--urgent)" : "var(--text-muted)",
        }}>
          {saveState === "saving"
            ? "Saving..."
            : saveState === "saved"
            ? "All changes saved"
            : saveState === "error"
            ? "Couldn't save (will retry on your next change)"
            : "Changes save automatically"}
        </span>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Poller and integration changes apply after a server restart.
        </span>
      </div>
    </div>
  );
}
