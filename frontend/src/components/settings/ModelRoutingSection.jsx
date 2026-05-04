import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

const ROUTING_RULES = [
  { key: "triage", label: "Triage (pupdate classification)", tier: "haiku" },
  { key: "classify", label: "Signal classification", tier: "haiku" },
  { key: "scene", label: "Scene creative note", tier: "haiku" },
  { key: "conflict_query", label: "Conflict detection", tier: "haiku" },
  { key: "skill", label: "Skills (default)", tier: "sonnet" },
  { key: "skill:pr-review", label: "PR Review", tier: "sonnet" },
  { key: "project_plan", label: "Project planning", tier: "sonnet" },
  { key: "profile_judge", label: "Task outcome judging", tier: "sonnet" },
  { key: "training:entry", label: "Training entries", tier: "opus" },
  { key: "training:judge", label: "Training judging", tier: "opus" },
  { key: "coding_agent", label: "Coding agents", tier: "opus" },
];

/**
 * Model Routing — Haiku/Sonnet/Opus per task family + the global
 * thinking-budget knob. Used by every LLM call across the system.
 */
export default function ModelRoutingSection({ config, setConfig, updateField }) {
  const [open, setOpen] = useState(false);

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>Model Routing</span>
      </div>
      {open && (
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
              {ROUTING_RULES.map(({ key, label, tier }) => (
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
  );
}
