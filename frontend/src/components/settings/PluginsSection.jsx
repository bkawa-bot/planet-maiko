import { useState } from "react";
import { ChevronDown, ChevronRight, Plug, AlertTriangle } from "@icons";
import { api } from "../../api/client";

/**
 * Plugins — list of discovered plugins (local files in ~/.maiko/plugins
 * or pip packages registered via the planet_maiko.plugins entry point)
 * plus their declared config_schema rendered as form fields.
 */
export default function PluginsSection({ config, setConfig, plugins, setPlugins, onMessage }) {
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
                  {p.config_schema && Object.keys(p.config_schema).length > 0 && (
                    <PluginConfigForm
                      plugin={p}
                      config={config}
                      onChange={(field, value) => {
                        const key = p.config_key || p.name;
                        const section = { ...(config[key] || {}) };
                        section[field] = value;
                        setConfig({ ...config, [key]: section });
                      }}
                    />
                  )}
                  {Array.isArray(p.setup_actions) && p.setup_actions.length > 0 && (
                    <PluginSetupActions plugin={p} onMessage={onMessage} />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/**
 * User-triggered setup actions (backfill, import, auto-configure).
 * Each fires POST /api/plugins/<name>/actions/<key>; the backend runs
 * it in a daemon thread and drops a memo when done, so the button
 * just kicks it off and tells the user it's running.
 */
function PluginSetupActions({ plugin, onMessage }) {
  const [running, setRunning] = useState(null);
  return (
    <div className="plugin-setup-actions">
      {plugin.setup_actions.map((a) => (
        <div key={a.key} className="plugin-setup-action">
          <button
            className="btn btn-sm"
            disabled={running === a.key}
            onClick={async () => {
              if (a.destructive && !window.confirm(`Run "${a.label}"? This can't be undone.`)) {
                return;
              }
              setRunning(a.key);
              try {
                await api.runPluginAction(plugin.name, a.key);
                onMessage(`"${a.label}" started — you'll get a memo when it finishes.`);
              } catch (err) {
                onMessage(`Couldn't start "${a.label}": ${err.message}`);
              } finally {
                setRunning(null);
              }
            }}
          >
            {running === a.key ? "Starting…" : a.label}
          </button>
          {a.description && (
            <span className="plugin-setup-action-help">{a.description}</span>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Renders a plugin's declared config_schema as editable fields. Reads
 * the current values from config[plugin.config_key] and writes through
 * onChange(field, value). Supports string, bool, number, and list (CSV).
 * Intentionally thin — more complex shapes (nested, dependent fields)
 * can be added as plugins request them.
 */
function PluginConfigForm({ plugin, config, onChange }) {
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
