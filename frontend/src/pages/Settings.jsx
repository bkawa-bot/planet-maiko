import { useEffect, useState } from "react";
import { api } from "../api/client";
import { BookOpen } from "lucide-react";
import ConceptsModal from "../components/ConceptsModal";
import IntegrationsSection from "../components/settings/IntegrationsSection";
import AutopilotSection from "../components/settings/AutopilotSection";
import HomeOverviewSection from "../components/settings/HomeOverviewSection";
import RepoChecksSection from "../components/settings/RepoChecksSection";
import AgentPreferencesSection from "../components/settings/AgentPreferencesSection";
import ModelRoutingSection from "../components/settings/ModelRoutingSection";
import SceneSection from "../components/settings/SceneSection";
import PluginsSection from "../components/settings/PluginsSection";
import WorktreeMaintenanceSection from "../components/settings/WorktreeMaintenanceSection";
import TeamRulesSection from "../components/settings/TeamRulesSection";
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
  const [saving, setSaving] = useState(false);
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

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.updateConfig(config);
      invalidateDefaultOrg();
      if (config?.scene?.latitude && config?.scene?.longitude) {
        await api.refreshScene().catch(() => {});
      }
      flash("Settings saved! Restart the server to apply poller changes.");
    } catch (err) {
      flash("Failed to save: " + err.message);
    }
    setSaving(false);
  };

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
                  placeholder="e.g. Brigitte"
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

      <IntegrationsSection
        config={config}
        updateField={updateField}
        pollerStatus={pollerStatus}
        onRunPoller={handleRunPoller}
        onMessage={flash}
      />

      <AgentPreferencesSection
        config={config}
        setConfig={setConfig}
        updateField={updateField}
        updateRoleInstructions={updateRoleInstructions}
      />

      <WorktreeMaintenanceSection config={config} setConfig={setConfig} />

      <TeamRulesSection />

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
      />

      <button className="btn-save" onClick={handleSave} disabled={saving}>
        {saving ? "Saving..." : "Save Settings"}
      </button>
    </div>
  );
}
