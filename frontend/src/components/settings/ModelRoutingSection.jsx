import { useState } from "react";
import { ChevronDown, ChevronRight } from "@icons";

// Each row picks a runtime (which agent runtime executes the task),
// a model (what the runtime asks for — Claude tier vs Ollama model
// name), and a reasoning effort. Defaults track agents/routing.py.
//
// runtimeDefault means "fall back to brain.runtime" (no per-task
// override). Internal tasks default to ollama so they don't burn
// Anthropic credit; everything else inherits the user's default.
const ROUTING_RULES = [
  { key: "triage", label: "Triage (pupdate classification)", tier: "haiku", effort: "low", runtime: "" },
  { key: "classify", label: "Signal classification", tier: "haiku", effort: "low", runtime: "" },
  { key: "scene", label: "Scene creative note", tier: "haiku", effort: "low", runtime: "ollama" },
  { key: "agent_bio", label: "Agent bio generation", tier: "haiku", effort: "low", runtime: "ollama" },
  { key: "conflict_query", label: "Conflict detection", tier: "haiku", effort: "low", runtime: "" },
  { key: "skill", label: "Skills (default)", tier: "sonnet", effort: "medium", runtime: "" },
  { key: "skill:pr-review", label: "PR Review", tier: "sonnet", effort: "medium", runtime: "" },
  { key: "skill:home-overview", label: "Home overview", tier: "opus", effort: "high", runtime: "ollama" },
  { key: "project_plan", label: "Project planning", tier: "sonnet", effort: "medium", runtime: "" },
  { key: "profile_judge", label: "Task outcome judging", tier: "sonnet", effort: "medium", runtime: "" },
  { key: "training:entry", label: "Training entries", tier: "opus", effort: "high", runtime: "" },
  { key: "training:judge", label: "Training judging", tier: "opus", effort: "high", runtime: "" },
  { key: "coding_agent", label: "Coding agents", tier: "opus", effort: "high", runtime: "" },
];

const RUNTIME_OPTIONS = [
  { value: "", label: "Default (brain.runtime)" },
  { value: "claude-code", label: "Claude Code (headless)" },
  { value: "claude-code-tmux", label: "Claude Code (tmux interactive)" },
  { value: "ollama", label: "Ollama (local)" },
];

/**
 * Model Routing — runtime + model + effort per task family. Used by
 * every LLM call across the system.
 *
 * Three columns: Runtime, Model, Effort.
 * - Runtime picks which AgentRuntime executes the task. Empty / Default
 *   means "use whatever brain.runtime is set to."
 * - Model is runtime-specific. Claude-based runtimes show a tier
 *   dropdown (haiku/sonnet/opus). Ollama shows a free-text input
 *   ("llama3.1:8b", "qwen2.5:32b", etc.) since the user picks any
 *   model they've pulled locally.
 * - Effort maps to Claude's --effort flag and to Ollama's
 *   temperature + max_tokens. Same scale either way.
 */
export default function ModelRoutingSection({ config, setConfig, updateField }) {
  const [open, setOpen] = useState(false);

  const updateRuntimeRule = (key, value) => {
    const runtime_rules = { ...(config.routing?.runtime_rules || {}) };
    if (value) runtime_rules[key] = value;
    else delete runtime_rules[key];
    setConfig((c) => ({ ...c, routing: { ...c?.routing, runtime_rules } }));
  };

  const getRuntimeForRule = (key, fallback) => {
    const explicit = (config.routing?.runtime_rules || {})[key];
    return explicit !== undefined ? explicit : fallback;
  };

  // Resolve which "effective runtime" a given rule renders against.
  // Empty + fallback empty → assume claude-style for the model widget.
  const effectiveRuntime = (key, fallback) => {
    const chosen = getRuntimeForRule(key, fallback);
    if (!chosen) return "claude-code";   // default falls back to Claude-style names
    return chosen;
  };

  const isOllama = (runtimeName) => runtimeName === "ollama";

  const getModelForRule = (key, runtimeName, tierFallback) => {
    if (isOllama(runtimeName)) {
      const perRuntime = (config.routing?.runtime_models || {})[runtimeName] || {};
      return perRuntime[key] ?? "";
    }
    return (config.routing?.rules || {})[key] || tierFallback;
  };

  const setModelForRule = (key, runtimeName, value) => {
    if (isOllama(runtimeName)) {
      const runtime_models = { ...(config.routing?.runtime_models || {}) };
      const forRuntime = { ...(runtime_models[runtimeName] || {}) };
      if (value) forRuntime[key] = value;
      else delete forRuntime[key];
      runtime_models[runtimeName] = forRuntime;
      setConfig((c) => ({ ...c, routing: { ...c?.routing, runtime_models } }));
    } else {
      const rules = { ...(config.routing?.rules || {}), [key]: value };
      setConfig((c) => ({ ...c, routing: { ...c?.routing, rules } }));
    }
  };

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
              Pick the runtime, model, and reasoning effort for each kind of work.
              Internal stuff (scene, bios, overview) defaults to Ollama so Maiko
              doesn't burn Anthropic credit on prose. Coding / review agents stay
              on Claude. Empty runtime means "use whatever brain.runtime is."
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
                Default model (Claude tier)
                <select value={config.routing?.default_model || "sonnet"} onChange={(e) => updateField("routing", "default_model", e.target.value)} className="routing-select">
                  <option value="haiku">Haiku</option>
                  <option value="sonnet">Sonnet</option>
                  <option value="opus">Opus</option>
                </select>
              </label>
              <label title="Catch-all effort — applies when a task has no per-rule override below.">
                Default effort
                <select value={config.routing?.thinking_budget || "medium"} onChange={(e) => updateField("routing", "thinking_budget", e.target.value)} className="routing-select">
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="max">Max</option>
                </select>
              </label>
            </div>
            <div className="routing-rules-table">
              <div className="routing-rules-header routing-rules-header-4col">
                <span>Task Type</span>
                <span>Runtime</span>
                <span>Model</span>
                <span>Effort</span>
              </div>
              {ROUTING_RULES.map(({ key, label, tier, effort, runtime }) => {
                const chosenRuntime = effectiveRuntime(key, runtime);
                const ollama = isOllama(chosenRuntime);
                return (
                  <div key={key} className="routing-rule-row routing-rule-row-4col">
                    <span className="routing-rule-label">{label}</span>
                    <select
                      className="routing-select"
                      value={getRuntimeForRule(key, runtime)}
                      onChange={(e) => updateRuntimeRule(key, e.target.value)}
                    >
                      {RUNTIME_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                    {ollama ? (
                      <input
                        type="text"
                        className="routing-select"
                        placeholder="llama3.1:8b"
                        value={getModelForRule(key, chosenRuntime, "")}
                        onChange={(e) => setModelForRule(key, chosenRuntime, e.target.value)}
                      />
                    ) : (
                      <select
                        className="routing-select"
                        value={getModelForRule(key, chosenRuntime, tier)}
                        onChange={(e) => setModelForRule(key, chosenRuntime, e.target.value)}
                      >
                        <option value="haiku">Haiku</option>
                        <option value="sonnet">Sonnet</option>
                        <option value="opus">Opus</option>
                      </select>
                    )}
                    <select
                      className="routing-select"
                      value={(config.routing?.effort_rules || {})[key] || effort}
                      onChange={(e) => {
                        const effort_rules = { ...(config.routing?.effort_rules || {}), [key]: e.target.value };
                        setConfig((c) => ({ ...c, routing: { ...c?.routing, effort_rules } }));
                      }}
                    >
                      <option value="low">Low</option>
                      <option value="medium">Medium</option>
                      <option value="high">High</option>
                      <option value="max">Max</option>
                    </select>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
