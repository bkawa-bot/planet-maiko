import { useState } from "react";
import { ChevronDown, ChevronRight, Plug, AlertTriangle, CheckCircle2, AlertCircle, Loader } from "@icons";
import { api } from "../../api/client";

/**
 * Plugins — every discovered plugin (builtin first-party integrations,
 * local files in ~/.maiko/plugins, or pip packages registered via the
 * planet_maiko.plugins entry point) with its declared config_schema
 * rendered as a form, its setup actions as buttons, and a poll-status
 * line for pollers. This is the single home for integration config;
 * the first-party ones (GitHub, Linear, Calendar, PagerDuty) drive
 * test-connection / discovery / team-pick through sync setup actions.
 */
export default function PluginsSection({
  config, setConfig, plugins, setPlugins, onMessage,
  pollerStatus = {}, onRunPoller,
}) {
  const [open, setOpen] = useState(false);

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>Plugins</span>
        {plugins.length > 0 && <span className="section-count">{plugins.length}</span>}
      </div>
      {open && (
        <div className="collapsible-body">
          {plugins.length === 0 ? (
            <p className="integration-note">
              No plugins detected. Drop <code>.py</code> files into <code>~/.maiko/plugins/</code> or install a pip package
              with the <code>planet_maiko.plugins</code> entry point.
            </p>
          ) : (
            <div className="plugins-list">
              {plugins.map((p) => (
                <PluginCard
                  key={p.name}
                  p={p}
                  config={config}
                  setConfig={setConfig}
                  setPlugins={setPlugins}
                  onMessage={onMessage}
                  poller={pollerStatus[p.name]}
                  onRunPoller={onRunPoller}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function PluginCard({ p, config, setConfig, setPlugins, onMessage, poller, onRunPoller }) {
  // Result text shown inline next to each sync action button.
  // { [actionKey]: { status: "ok"|"error"|"running", text } }
  const [actionState, setActionState] = useState({});
  // Options harvested from a sync action's `options` payload, keyed by
  // the config field they populate (drives `options_from` selects).
  const [dynamicOptions, setDynamicOptions] = useState({});
  const [pollerBusy, setPollerBusy] = useState(false);

  const configKey = p.config_key || p.name;

  const applyConfigPatch = (patch) => {
    setConfig({
      ...config,
      [configKey]: { ...(config[configKey] || {}), ...patch },
    });
  };

  const runAction = async (a) => {
    if (a.destructive && !window.confirm(`Run "${a.label}"? This can't be undone.`)) {
      return;
    }
    if (a.sync) {
      setActionState((s) => ({ ...s, [a.key]: { status: "running", text: "Working…" } }));
      try {
        const r = await api.runPluginAction(p.name, a.key);
        const ok = r?.ok !== false;
        setActionState((s) => ({
          ...s,
          [a.key]: { status: ok ? "ok" : "error", text: r?.message || (ok ? "Done." : "Failed") },
        }));
        if (ok && r?.config_patch) applyConfigPatch(r.config_patch);
        if (r?.options) setDynamicOptions((o) => ({ ...o, ...r.options }));
      } catch (err) {
        setActionState((s) => ({
          ...s,
          [a.key]: { status: "error", text: err.message || "Failed" },
        }));
      }
      return;
    }
    // Async: fire-and-forget, backend drops a memo when it finishes.
    try {
      await api.runPluginAction(p.name, a.key);
      onMessage(`"${a.label}" started — you'll get a memo when it finishes.`);
    } catch (err) {
      onMessage(`Couldn't start "${a.label}": ${err.message}`);
    }
  };

  const hasSchema = p.config_schema && Object.keys(p.config_schema).length > 0;
  const actions = Array.isArray(p.setup_actions) ? p.setup_actions : [];

  return (
    <div className={`plugin-card ${p.status}`}>
      <div className="plugin-card-header">
        <Plug size={14} className={`plugin-icon status-${p.status}`} />
        <div className="plugin-info">
          <span className="plugin-name">{p.name}</span>
          <span className="plugin-source">
            {p.source === "local"
              ? p.file
              : p.source === "builtin"
                ? "built-in"
                : `entry_point: ${p.entry_point}`}
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
                  onMessage(`Plugin "${p.name}" ${result.status}. Restart the server to apply.`);
                  const updated = await api.getPlugins();
                  setPlugins(updated);
                } catch (err) {
                  onMessage("Failed to toggle plugin: " + err.message);
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

      {hasSchema && (
        <PluginConfigForm
          plugin={p}
          config={config}
          dynamicOptions={dynamicOptions}
          onChange={(field, value) => {
            setConfig({
              ...config,
              [configKey]: { ...(config[configKey] || {}), [field]: value },
            });
          }}
        />
      )}

      {actions.length > 0 && (
        <div className="plugin-setup-actions">
          {actions.map((a) => {
            const r = actionState[a.key];
            return (
              <div key={a.key} className="plugin-setup-action">
                <button
                  className="btn btn-sm"
                  disabled={r?.status === "running"}
                  onClick={() => runAction(a)}
                >
                  {r?.status === "running" ? "Working…" : a.label}
                </button>
                {a.description && (
                  <span className="plugin-setup-action-help">{a.description}</span>
                )}
                {r && r.status !== "running" && (
                  <span className={`test-result test-result-${r.status}`}>
                    {r.status === "ok"
                      ? <CheckCircle2 size={11} />
                      : <AlertCircle size={11} />}
                    {" "}{r.text}
                  </span>
                )}
                {r && r.status === "running" && (
                  <span className="test-result test-result-testing">
                    <Loader size={11} className="spin" /> {r.text}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {poller && (
        <div className="poller-status poller-status-ok">
          <div className="poller-status-row">
            <span>
              {poller.enabled ? "Enabled" : "Disabled"}
              {" · polls every "}{poller.interval_minutes || 5} min
            </span>
            {onRunPoller && (
              <button
                disabled={pollerBusy}
                onClick={async () => {
                  setPollerBusy(true);
                  try { await onRunPoller(p.name); } finally { setPollerBusy(false); }
                }}
              >
                {pollerBusy ? "Running…" : "Run Now"}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Renders a plugin's declared config_schema as editable fields. Reads
 * current values from config[plugin.config_key] and writes through
 * onChange(field, value). Supports string, bool, number, list (CSV),
 * and select (static `options` or `options_from` a sync setup action,
 * whose results arrive via the `dynamicOptions` prop).
 */
function PluginConfigForm({ plugin, config, onChange, dynamicOptions = {} }) {
  const key = plugin.config_key || plugin.name;
  const section = config?.[key] || {};
  const schema = plugin.config_schema || {};
  return (
    <div className="plugin-config-form">
      {Object.entries(schema).map(([field, meta]) => {
        const value = section[field];
        const type = meta.type || "string";
        const label = meta.label || field;
        if (type === "bool") {
          return (
            <label key={field} className="plugin-config-field plugin-config-bool">
              <input
                type="checkbox"
                checked={!!value}
                onChange={(e) => onChange(field, e.target.checked)}
              />
              <span>{label}</span>
              {meta.help && <span className="plugin-config-help">— {meta.help}</span>}
            </label>
          );
        }
        if (type === "select") {
          const fetched = dynamicOptions[field];
          let options = meta.options || fetched || [];
          // No options yet but a value is saved: show it as the sole
          // choice so a configured field survives a reload until the
          // user re-runs the populating action.
          if ((!options || options.length === 0) && value) {
            options = [{ value, label: String(value) }];
          }
          return (
            <label key={field} className="plugin-config-field">
              <span>{label}{meta.help && <span className="plugin-config-help"> — {meta.help}</span>}</span>
              <select value={value ?? ""} onChange={(e) => onChange(field, e.target.value)}>
                <option value="">— none —</option>
                {options.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
          );
        }
        if (type === "list") {
          const csv = Array.isArray(value) ? value.join(", ") : (value || "");
          return (
            <label key={field} className="plugin-config-field">
              <span>{label}{meta.help && <span className="plugin-config-help"> — {meta.help}</span>}</span>
              <input
                type="text"
                value={csv}
                placeholder={meta.placeholder || "comma, separated"}
                onChange={(e) => onChange(
                  field,
                  e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                )}
              />
            </label>
          );
        }
        // string / number fallthrough
        return (
          <label key={field} className="plugin-config-field">
            <span>{label}{meta.help && <span className="plugin-config-help"> — {meta.help}</span>}</span>
            <input
              type={meta.secret ? "password" : (type === "number" ? "number" : "text")}
              value={value ?? ""}
              placeholder={meta.placeholder || ""}
              onChange={(e) => {
                const raw = e.target.value;
                onChange(field, type === "number" && raw !== "" ? Number(raw) : raw);
              }}
            />
          </label>
        );
      })}
    </div>
  );
}
