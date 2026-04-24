import { useEffect, useState } from "react";
import { X, Plus, Trash2, Loader, Save } from "lucide-react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import "./AutomationEditor.css";

/**
 * Full create/edit modal for Automations. Reads CONDITION_SCHEMAS +
 * ACTION_SCHEMAS to render structured forms per condition/action kind
 * instead of a raw JSON editor.
 *
 * Two modes:
 *   mode="create" — blank form + scope picker
 *   mode="edit"   — prefilled from an existing Automation
 *
 * Props:
 *   automation  — (edit mode) the row to edit
 *   onClose     — () => void, cancel / close without saving
 *   onSaved     — (saved) => void, called after successful save/delete
 */
export default function AutomationEditor({ mode = "edit", automation, onClose, onSaved }) {
  const [name, setName] = useState(automation?.name || "");
  const [description, setDescription] = useState(automation?.description || "");
  const [scopeRepo, setScopeRepo] = useState(automation?.scope_repo || "");
  const [status, setStatus] = useState(automation?.status || "active");
  const [cooldownDays, setCooldownDays] = useState(
    automation?.cooldown_days != null ? automation.cooldown_days : 7,
  );
  const [whenLogic, setWhenLogic] = useState(automation?.when_logic || "all");
  const [when, setWhen] = useState(() => {
    const src = automation?.when?.length ? automation.when : [{ kind: "", config: {} }];
    return src.map((c) => ({ kind: c.kind || "", config: { ...(c.config || {}) } }));
  });
  const [then, setThen] = useState(() => {
    const src = automation?.then?.length ? automation.then : [{ kind: "", config: {} }];
    return src.map((a) => ({ kind: a.kind || "", config: { ...(a.config || {}) } }));
  });
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [pupdateTypes, setPupdateTypes] = useState(null);
  const [pupdateSources, setPupdateSources] = useState([]);
  const [configuredRepos, setConfiguredRepos] = useState([]);
  const [skills, setSkills] = useState([]);

  // Fetch authoritative dropdown data once per editor open so plugins
  // and config changes propagate without a page reload. All failures
  // are silent — the editor still works with raw text inputs.
  useEffect(() => {
    let cancelled = false;
    api.getPupdateTypes()
      .then((types) => {
        if (cancelled || !Array.isArray(types)) return;
        setPupdateTypes(types.map((t) => ({
          value: t.name,
          label: t.label || t.name,
          group: t.group || "Other",
        })));
      })
      .catch(() => {});
    api.getPupdateSources()
      .then((sources) => {
        if (cancelled || !Array.isArray(sources)) return;
        setPupdateSources(sources.map((s) => s.name));
      })
      .catch(() => {});
    api.getConfig()
      .then((cfg) => {
        if (cancelled) return;
        setConfiguredRepos((cfg?.github?.repos) || []);
      })
      .catch(() => {});
    api.getSkills()
      .then((list) => {
        if (cancelled || !Array.isArray(list)) return;
        setSkills(list);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Agent-role kinds + skills from the backend, merged. Roles are
  // static (cartograph/investigation/repo_analysis aren't skills —
  // they're executor dispatch keys); skills are dynamic so custom
  // skills added via /api/skills show up without a frontend change.
  const agentJobKinds = [
    { value: "cartograph", label: "cartograph — walk the repo" },
    { value: "investigation", label: "investigation — spawn an investigator" },
    { value: "repo_analysis", label: "repo_analysis — read-only investigation" },
    ...skills.map((s) => ({
      value: s.id || s.name,
      label: `${s.name}${s.is_default ? " (skill)" : " (custom skill)"}`,
    })),
  ];

  // Resolve a schema's string-type fields with a `datalist` tag to
  // their live option list. ConditionRow / ActionRow pass this down
  // to DynamicFields so a field can say `datalist: "repos"` and get
  // the configured repo list without the schema knowing about state.
  const datalists = {
    repos: configuredRepos,
    sources: pupdateSources,
  };
  // Select options for the specialty_id field — empty option means
  // "no specialty, just the base role". Backed by the same skills
  // fetch that powers agent_job_kinds.
  const specialtyOptions = [
    { value: "", label: "— base role only (no specialty) —" },
    ...skills.map((s) => ({
      value: s.id || s.name,
      label: s.name || s.id,
    })),
  ];

  // Same pattern for select fields — `optionsKey: "agent_job_kinds"`
  // resolves against this map so the schema stays static while the
  // real option list reflects currently-registered skills.
  const optionsMap = {
    agent_job_kinds: agentJobKinds,
    specialties: specialtyOptions,
  };

  // Scope is derived from the chosen condition kinds — the user never
  // picks it directly. Any condition that can fire per-pupdate makes
  // the automation pupdate-scoped (today that's just pupdate_match).
  // Everything else is cycle-scoped. Drives which actions are
  // compatible below.
  const scope = when.some((c) => CONDITION_SCHEMAS[c.kind]?.scopes?.includes("pupdate"))
    ? "pupdate"
    : "cycle";

  const conditionOptions = Object.entries(CONDITION_SCHEMAS)
    .map(([kind, s]) => ({ value: kind, label: s.label, group: s.group || "Other" }));
  const actionOptions = Object.entries(ACTION_SCHEMAS)
    .filter(([, s]) => s.scopes.includes(scope))
    .map(([kind, s]) => ({ value: kind, label: s.label, group: s.group || "Other" }));

  // If the chosen condition forces pupdate-scope, incompatible action
  // kinds become nonsense — clear them so the user re-picks something
  // valid. Runs once when scope flips.
  useEffect(() => {
    setThen((rows) => rows.map((a) => {
      const schema = ACTION_SCHEMAS[a.kind];
      if (!schema || !schema.scopes.includes(scope)) {
        return { kind: "", config: {} };
      }
      return a;
    }));
  }, [scope]);

  const isValid = () => {
    if (!name.trim()) return false;
    if (!when.length || when.some((c) => !c.kind)) return false;
    if (!then.length || then.some((a) => !a.kind)) return false;
    return true;
  };

  const handleSave = async () => {
    if (!isValid()) {
      showToast("Fill in the name + at least one trigger + one action", "high");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        description: description.trim(),
        // Derived from the chosen conditions — never user-picked.
        execution_scope: scope,
        scope_repo: scopeRepo.trim() || null,
        status,
        cooldown_days: scope === "pupdate" ? 0 : Number(cooldownDays || 0),
        when_logic: whenLogic,
        when: when.map((c) => ({ kind: c.kind, config: c.config || {} })),
        then: then.map((a) => ({ kind: a.kind, config: a.config || {} })),
      };
      let saved;
      if (mode === "create") {
        saved = await api.createAutomation(payload);
      } else {
        saved = await api.updateAutomation(automation.id, payload);
      }
      showToast(mode === "create" ? "Automation created" : "Automation saved", "normal");
      onSaved?.(saved);
    } catch (err) {
      showToast(err.message || "Save failed", "high");
    }
    setSaving(false);
  };

  const handleDelete = async () => {
    if (mode !== "edit") return;
    if (!window.confirm(`Delete "${automation.name}"? This is permanent. Seeded defaults will re-seed on next restart.`)) return;
    setDeleting(true);
    try {
      await api.deleteAutomation(automation.id);
      showToast("Automation deleted", "normal");
      onSaved?.(null);
    } catch (err) {
      showToast(err.message || "Delete failed", "high");
    }
    setDeleting(false);
  };

  const updateWhen = (idx, patch) => {
    setWhen((rows) => rows.map((r, i) => i === idx ? { ...r, ...patch } : r));
  };
  const updateThen = (idx, patch) => {
    setThen((rows) => rows.map((r, i) => i === idx ? { ...r, ...patch } : r));
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="automation-editor" onClick={(e) => e.stopPropagation()}>
        <div className="automation-editor-header">
          <h3>{mode === "create" ? "New Automation" : "Edit Automation"}</h3>
          <button className="btn-ghost" onClick={onClose} title="Close"><X size={14} /></button>
        </div>

        <div className="automation-editor-body">
          <div className="automation-editor-field">
            <label>Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Short, descriptive"
              autoFocus
            />
          </div>

          <div className="automation-editor-field">
            <label>Description</label>
            <textarea
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional — what is this for, why does it exist"
            />
          </div>

          <div className="automation-editor-section">
            <div className="automation-editor-section-header">
              <span className="automation-editor-section-label">WHEN</span>
              {scope === "cycle" && when.length > 1 && (
                <div className="when-logic-toggle">
                  <label className={whenLogic === "all" ? "active" : ""}>
                    <input type="radio" checked={whenLogic === "all"} onChange={() => setWhenLogic("all")} /> all match
                  </label>
                  <label className={whenLogic === "any" ? "active" : ""}>
                    <input type="radio" checked={whenLogic === "any"} onChange={() => setWhenLogic("any")} /> any match
                  </label>
                </div>
              )}
            </div>
            {when.map((c, idx) => (
              <ConditionRow
                key={idx}
                condition={c}
                options={conditionOptions}
                pupdateTypes={pupdateTypes}
                datalists={datalists}
                optionsMap={optionsMap}
                onChange={(patch) => updateWhen(idx, patch)}
                onRemove={when.length > 1 ? () => setWhen(when.filter((_, i) => i !== idx)) : null}
              />
            ))}
            {scope === "cycle" && (
              <button
                className="btn btn-sm automation-editor-add"
                onClick={() => setWhen([...when, { kind: "", config: {} }])}
              >
                <Plus size={10} /> Add another condition
              </button>
            )}
            {scope === "pupdate" && (
              <div className="automation-editor-hint">
                Per-pupdate triggers take a single matcher — actions below operate on the matched pupdate.
              </div>
            )}
          </div>

          <div className="automation-editor-section">
            <div className="automation-editor-section-header">
              <span className="automation-editor-section-label">THEN</span>
            </div>
            {then.map((a, idx) => (
              <ActionRow
                key={idx}
                action={a}
                options={actionOptions}
                datalists={datalists}
                optionsMap={optionsMap}
                onChange={(patch) => updateThen(idx, patch)}
                onRemove={then.length > 1 ? () => setThen(then.filter((_, i) => i !== idx)) : null}
              />
            ))}
            <button
              className="btn btn-sm automation-editor-add"
              onClick={() => setThen([...then, { kind: "", config: {} }])}
            >
              <Plus size={10} /> Add another action
            </button>
          </div>

          <div className="automation-editor-row">
            <div className="automation-editor-field">
              <label>Repo scope (optional)</label>
              <input
                type="text"
                value={scopeRepo}
                onChange={(e) => setScopeRepo(e.target.value)}
                placeholder="org/repo — helps with filtering"
                list={configuredRepos.length ? "automation-editor-scope-repos" : undefined}
              />
              {configuredRepos.length > 0 && (
                <datalist id="automation-editor-scope-repos">
                  {configuredRepos.map((r) => <option key={r} value={r} />)}
                </datalist>
              )}
            </div>
            {scope === "cycle" && (
              <div className="automation-editor-field">
                <label>Cooldown (days)</label>
                <input
                  type="number"
                  min="0"
                  value={cooldownDays}
                  onChange={(e) => setCooldownDays(e.target.value)}
                />
              </div>
            )}
            <div className="automation-editor-field">
              <label>Status</label>
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="active">Active</option>
                <option value="paused">Paused</option>
                <option value="archived">Archived</option>
              </select>
            </div>
          </div>
        </div>

        <div className="automation-editor-footer">
          {mode === "edit" && automation?.created_by !== "seed" && (
            <button
              className="btn btn-sm btn-danger"
              onClick={handleDelete}
              disabled={deleting || saving}
            >
              {deleting ? <Loader size={10} className="spin" /> : <Trash2 size={10} />} Delete
            </button>
          )}
          <span style={{ flex: 1 }} />
          <button className="btn btn-sm" onClick={onClose} disabled={saving}>Cancel</button>
          <button
            className="btn btn-sm btn-primary"
            onClick={handleSave}
            disabled={saving || !isValid()}
          >
            {saving ? <Loader size={10} className="spin" /> : <Save size={10} />} Save
          </button>
        </div>
      </div>
    </div>
  );
}


// --------------------------------------------------------------------------
// ConditionRow + ActionRow — kind dropdown + dynamic field renderer.
// --------------------------------------------------------------------------

function GroupedOptions({ options }) {
  // Stable-order the groups as they first appear in the list so "Time"
  // stays above "Coverage state" etc. without alphabetizing unexpectedly.
  const groupOrder = [];
  const byGroup = {};
  for (const opt of options) {
    if (!(opt.group in byGroup)) {
      groupOrder.push(opt.group);
      byGroup[opt.group] = [];
    }
    byGroup[opt.group].push(opt);
  }
  return (
    <>
      {groupOrder.map((g) => (
        <optgroup key={g} label={g}>
          {byGroup[g].map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </optgroup>
      ))}
    </>
  );
}

function ConditionRow({ condition, options, pupdateTypes, datalists, optionsMap, onChange, onRemove }) {
  let schema = CONDITION_SCHEMAS[condition.kind];
  // pupdate_match's `type` field needs the live list (built-ins plus
  // anything plugins registered). Override the field's options when
  // the fetch has returned; otherwise fall back to whatever the schema
  // was built with.
  if (schema && pupdateTypes && pupdateTypes.length > 0) {
    schema = {
      ...schema,
      fields: schema.fields.map((f) =>
        f.name === "type" && f.type === "select"
          ? { ...f, options: pupdateTypes }
          : f
      ),
    };
  }
  return (
    <div className="automation-entry-row">
      <div className="automation-entry-top">
        <select
          className="automation-entry-kind"
          value={condition.kind}
          onChange={(e) => onChange({ kind: e.target.value, config: defaultConfigFor(CONDITION_SCHEMAS[e.target.value]) })}
        >
          <option value="">Select a trigger…</option>
          <GroupedOptions options={options} />
        </select>
        {onRemove && (
          <button className="btn-ghost automation-entry-remove" onClick={onRemove} title="Remove">
            <X size={12} />
          </button>
        )}
      </div>
      {schema && <DynamicFields schema={schema} config={condition.config} datalists={datalists} optionsMap={optionsMap} onChange={(config) => onChange({ config })} />}
    </div>
  );
}

function ActionRow({ action, options, datalists, optionsMap, onChange, onRemove }) {
  const schema = ACTION_SCHEMAS[action.kind];
  return (
    <div className="automation-entry-row">
      <div className="automation-entry-top">
        <select
          className="automation-entry-kind"
          value={action.kind}
          onChange={(e) => onChange({ kind: e.target.value, config: defaultConfigFor(ACTION_SCHEMAS[e.target.value]) })}
        >
          <option value="">Select an action…</option>
          <GroupedOptions options={options} />
        </select>
        {onRemove && (
          <button className="btn-ghost automation-entry-remove" onClick={onRemove} title="Remove">
            <X size={12} />
          </button>
        )}
      </div>
      {schema && <DynamicFields schema={schema} config={action.config} datalists={datalists} optionsMap={optionsMap} onChange={(config) => onChange({ config })} />}
    </div>
  );
}


function DynamicFields({ schema, config, datalists, optionsMap, onChange }) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  if (!schema || !schema.fields?.length) return null;

  const setField = (name, value) => {
    // Support dotted paths like "draft.title" — write into nested shape.
    if (!name.includes(".")) {
      onChange({ ...config, [name]: value });
      return;
    }
    const [head, ...rest] = name.split(".");
    const nested = { ...(config[head] || {}) };
    let cursor = nested;
    for (let i = 0; i < rest.length - 1; i++) {
      cursor[rest[i]] = { ...(cursor[rest[i]] || {}) };
      cursor = cursor[rest[i]];
    }
    cursor[rest[rest.length - 1]] = value;
    onChange({ ...config, [head]: nested });
  };
  const getField = (name) => {
    if (!name.includes(".")) return config[name];
    const parts = name.split(".");
    let cursor = config;
    for (const p of parts) {
      if (cursor == null) return undefined;
      cursor = cursor[p];
    }
    return cursor;
  };

  const basicFields = schema.fields.filter((f) => !f.advanced);
  const advancedFields = schema.fields.filter((f) => f.advanced);

  // Auto-expand advanced when any advanced field already has a value —
  // otherwise the user opens their existing row and can't see settings
  // they previously configured.
  const hasAdvancedValue = advancedFields.some((f) => {
    const v = getField(f.name);
    return v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0);
  });
  const expanded = showAdvanced || hasAdvancedValue;

  return (
    <div className="automation-entry-fields">
      {schema.help && <div className="automation-entry-help">{schema.help}</div>}
      {basicFields.map((f) => (
        <FieldInput key={f.name} field={f} value={getField(f.name)} datalists={datalists} optionsMap={optionsMap} onChange={(v) => setField(f.name, v)} />
      ))}
      {advancedFields.length > 0 && (
        <div className="automation-entry-advanced">
          {!expanded ? (
            <button
              type="button"
              className="automation-entry-advanced-toggle"
              onClick={() => setShowAdvanced(true)}
            >
              + {advancedFields.length} more option{advancedFields.length === 1 ? "" : "s"}
            </button>
          ) : (
            <>
              <div className="automation-entry-advanced-label">Advanced</div>
              {advancedFields.map((f) => (
                <FieldInput key={f.name} field={f} value={getField(f.name)} datalists={datalists} optionsMap={optionsMap} onChange={(v) => setField(f.name, v)} />
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function FieldInput({ field, value, datalists, optionsMap, onChange }) {
  const v = value != null ? value : (field.default != null ? field.default : "");
  if (field.type === "bool") {
    return (
      <label className="automation-field automation-field-bool">
        <input type="checkbox" checked={!!v} onChange={(e) => onChange(e.target.checked)} />
        <span>{field.label}{field.help && <small> — {field.help}</small>}</span>
      </label>
    );
  }
  if (field.type === "select") {
    // `optionsKey` lets a schema reference a dynamic list (e.g.
    // "agent_job_kinds" → built-in kinds + custom skills) without
    // the schema having to know about state.
    const options = field.options
      || (field.optionsKey && optionsMap?.[field.optionsKey])
      || [];
    return (
      <label className="automation-field">
        <span>{field.label}{field.help && <small> — {field.help}</small>}</span>
        <select value={v || ""} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">(none)</option>
          {options.map((opt) => (
            <option key={opt.value || opt} value={opt.value || opt}>{opt.label || opt}</option>
          ))}
        </select>
      </label>
    );
  }
  if (field.type === "list") {
    const csv = Array.isArray(v) ? v.join(", ") : (v || "");
    return (
      <label className="automation-field">
        <span>{field.label}{field.help && <small> — {field.help}</small>}</span>
        <input
          type="text"
          value={csv}
          placeholder={field.placeholder || "item1, item2"}
          onChange={(e) => onChange(
            e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
          )}
        />
      </label>
    );
  }
  if (field.type === "number") {
    return (
      <label className="automation-field">
        <span>{field.label}{field.help && <small> — {field.help}</small>}</span>
        <input
          type="number"
          value={v}
          min={field.min}
          max={field.max}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
      </label>
    );
  }
  if (field.type === "textarea") {
    return (
      <label className="automation-field">
        <span>{field.label}{field.help && <small> — {field.help}</small>}</span>
        <textarea
          rows={field.rows || 3}
          value={v || ""}
          placeholder={field.placeholder || ""}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
    );
  }
  // string default — supports a datalist hint for autocomplete
  // (`datalist: "repos"` or `datalist: "sources"`). Users can still
  // type a value that isn't in the list; the list is a suggestion.
  const datalistValues = field.datalist && datalists ? datalists[field.datalist] : null;
  const listId = datalistValues?.length
    ? `field-datalist-${field.datalist}-${field.name}`
    : undefined;
  return (
    <label className="automation-field">
      <span>{field.label}{field.help && <small> — {field.help}</small>}</span>
      <input
        type="text"
        value={v || ""}
        placeholder={field.placeholder || ""}
        onChange={(e) => onChange(e.target.value)}
        list={listId}
      />
      {listId && (
        <datalist id={listId}>
          {datalistValues.map((opt) => <option key={opt} value={opt} />)}
        </datalist>
      )}
    </label>
  );
}


function defaultConfigFor(schema) {
  if (!schema || !schema.fields) return {};
  const out = {};
  for (const f of schema.fields) {
    if (f.default === undefined) continue;
    if (f.name.includes(".")) {
      const [head, ...rest] = f.name.split(".");
      if (!out[head]) out[head] = {};
      let cursor = out[head];
      for (let i = 0; i < rest.length - 1; i++) {
        if (!cursor[rest[i]]) cursor[rest[i]] = {};
        cursor = cursor[rest[i]];
      }
      cursor[rest[rest.length - 1]] = f.default;
    } else {
      out[f.name] = f.default;
    }
  }
  return out;
}


// --------------------------------------------------------------------------
// Schemas — source of truth for the form builder. Adding a new
// condition/action kind means registering its backend handler in
// brain/automations/__init__.py AND adding an entry here.
// --------------------------------------------------------------------------

// Fallback pupdate-type list used only when /api/pupdate-types is
// unreachable (first render before the fetch resolves, or API down).
// The authoritative list lives in src/planet_maiko/pupdate_types.py
// and is augmented at runtime by any plugin that implements
// register_pupdate_types().
const PUPDATE_TYPE_OPTIONS = [
  "pr_review_requested", "pr_changes_requested", "pr_approved", "pr_merged",
  "pr_ci_passed", "pr_ci_failed", "pr_review_commented",
  "linear_assigned", "linear_mention", "linear_status_changed",
  "calendar_event", "calendar_1on1",
  "agent_ready_for_review", "agent_plan_for_approval", "agent_stuck",
  "agent_proposal", "incident", "error_spike", "deploy_rollback",
  "deploy_blocked", "deploy_stuck", "batch_job_failing",
];

const PRIORITY_OPTIONS = [
  { value: "low", label: "low" },
  { value: "normal", label: "normal" },
  { value: "high", label: "high" },
  { value: "urgent", label: "urgent" },
];

// Task types the USER owns (Tasks page). Separate from agent-job kinds.
const TASK_TYPE_OPTIONS = [
  { value: "todo", label: "todo (generic)" },
  { value: "bug", label: "bug" },
  { value: "feature", label: "feature" },
  { value: "coding", label: "coding (you'll assign an agent later)" },
  { value: "review", label: "review (you owe someone a review)" },
];

const CONDITION_SCHEMAS = {
  cadence: {
    label: "On a schedule",
    group: "Time",
    scopes: ["cycle"],
    help: "Fires every N minutes. Uses last_fired_at + interval as the clock.",
    fields: [
      { name: "interval_minutes", type: "number", label: "Every N minutes", default: 60, min: 1 },
    ],
  },
  overview_stale: {
    label: "A repo's overview goes stale",
    group: "Coverage state",
    scopes: ["cycle"],
    help: "Fires when a repo's cartographer insight is missing or older than the threshold.",
    fields: [
      { name: "repo", type: "string", label: "Repo (org/name)", placeholder: "org/repo", help: "required", datalist: "repos" },
      { name: "stale_days", type: "number", label: "Days before stale", default: 30, min: 1 },
    ],
  },
  lora_missing: {
    label: "Enough rules pile up without a LoRA",
    group: "Coverage state",
    scopes: ["cycle"],
    help: "Fires when a repo has N+ active Learnings and no AgentProfile for that scope has an adapter_path set.",
    fields: [
      { name: "repo", type: "string", label: "Repo (org/name)", placeholder: "org/repo", datalist: "repos" },
      { name: "min_learnings", type: "number", label: "Min active learnings", default: 10, min: 1 },
    ],
  },
  pupdate_chain: {
    label: "A chain of pupdate types lands together",
    group: "Events",
    scopes: ["cycle"],
    help: "Fires when all the listed pupdate types appear within the time window, grouped by the same key (usually repo).",
    fields: [
      { name: "types", type: "list", label: "Pupdate types (all required)", placeholder: "pr_ci_failed, error_spike", help: "comma-separated" },
      { name: "within_minutes", type: "number", label: "Window (minutes)", default: 30, min: 1 },
      { name: "group_by", type: "select", label: "Group by", default: "repo", options: ["repo", "tag"], advanced: true },
    ],
  },
  pupdate_match: {
    label: "A pupdate of a specific type comes in",
    group: "Events",
    scopes: ["cycle", "pupdate"],
    help: "Picks up individual incoming pupdates. Type is the most common filter; the other fields narrow further when you need it.",
    fields: [
      { name: "type", type: "select", label: "Type", options: PUPDATE_TYPE_OPTIONS, help: "the pupdate's type field" },
      { name: "title_contains", type: "string", label: "Title contains", placeholder: "substring, case-insensitive", help: "optional extra filter on the title" },
      // Everything below is rare — collapsed under Advanced.
      { name: "source", type: "string", label: "Source", placeholder: "github, linear, calendar…", advanced: true, datalist: "sources", help: "poller name (auto-suggested from your configured pollers)" },
      { name: "type_prefix", type: "string", label: "Type prefix", placeholder: "pr_", advanced: true, help: "match a family of types (e.g. pr_*)" },
      { name: "priority", type: "select", label: "Priority", options: PRIORITY_OPTIONS, advanced: true },
      { name: "actionable", type: "bool", label: "Must be actionable", advanced: true },
      { name: "has_tag", type: "string", label: "Has tag", placeholder: "e.g. ci", advanced: true },
      { name: "within_minutes", type: "number", label: "Window (minutes, cycle-scope only)", help: "how far back to scan in cycle scope; default 60", advanced: true },
    ],
  },
};

const ACTION_SCHEMAS = {
  run_agent_job: {
    label: "Run an agent job (pack-owned)",
    group: "Do work",
    scopes: ["cycle"],
    help: "Spawn an agent to do a one-shot task — cartograph a repo, investigate an incident, run a scheduled skill. Pack-owned: lands on the Agents page, not the Tasks list.",
    fields: [
      { name: "kind", type: "select", label: "Kind", default: "cartograph", optionsKey: "agent_job_kinds" },
      { name: "ask_first", type: "bool", label: "Ask me before running", help: "when on, the job waits for your approval; off runs it directly." },
      { name: "title", type: "string", label: "Title", placeholder: "Can template {service} etc." },
      { name: "description", type: "textarea", label: "Description / input", rows: 2, help: "skill input / what the agent should focus on" },
      { name: "scope_repo", type: "string", label: "Repo", placeholder: "org/repo or {service}", advanced: true, datalist: "repos" },
      { name: "specialty_id", type: "select", label: "Specialty", optionsKey: "specialties", advanced: true, help: "Extra context layered onto the agent's role. Silently dropped if the resolved agent doesn't have it attached." },
      { name: "priority", type: "select", label: "Priority", default: "normal", options: PRIORITY_OPTIONS, advanced: true },
    ],
  },
  notify_me: {
    label: "Notify me",
    group: "Let me know",
    scopes: ["cycle", "pupdate"],
    help: "Drops a notification on the Home page. Use when you just want to be told something happened, no task or agent spawn. Dismissable.",
    fields: [
      { name: "title", type: "string", label: "Title", placeholder: "e.g. 'CI has been red for 30 min' or '{pupdate_title}'", help: "Defaults to the triggering pupdate's title. Supports tokens like {pupdate_title}, {repo}." },
      { name: "body", type: "textarea", label: "Body", rows: 2, help: "Optional extra detail. Markdown. Supports {pupdate_body}, {pupdate_url}, {repo}." },
      { name: "priority", type: "select", label: "Priority", default: "normal", options: PRIORITY_OPTIONS, advanced: true },
      { name: "url", type: "string", label: "Click-through URL", placeholder: "https:// or {pupdate_url}", advanced: true },
    ],
  },
  create_task: {
    label: "Create a task (user-owed)",
    group: "Do work",
    scopes: ["cycle"],
    help: "Create a task you own — a todo / bug / feature that lives on the Tasks page. Use this when the work surfaces to you, not the pack.",
    fields: [
      { name: "title", type: "string", label: "Title" },
      { name: "type", type: "select", label: "Task type", default: "todo", options: TASK_TYPE_OPTIONS },
      { name: "description", type: "textarea", label: "Description", rows: 2 },
      { name: "auto_launch", type: "bool", label: "Launch an agent immediately", help: "For review/investigation/cartograph/repo_analysis types: skip manual Assign and spawn a linked agent job. No-op on todo/bug/feature." },
      { name: "repo", type: "string", label: "Repo", placeholder: "org/repo", advanced: true, datalist: "repos" },
      { name: "priority", type: "select", label: "Priority", default: "normal", options: PRIORITY_OPTIONS, advanced: true },
    ],
  },
  dismiss_pupdate: {
    label: "Dismiss it (archive)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Archives the pupdate. Pure noise-reduction.",
    fields: [],
  },
  create_task_from_pupdate: {
    label: "Create a task from it (user-owed)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Uses the pupdate's title/priority as the task seed. Lands on the Tasks page as work you own.",
    fields: [
      { name: "task_type", type: "select", label: "Task type", default: "todo", options: TASK_TYPE_OPTIONS },
      { name: "task_priority", type: "select", label: "Task priority", options: PRIORITY_OPTIONS, advanced: true },
    ],
  },
  spawn_agent_job_from_pupdate: {
    label: "Spawn an agent job from it (pack-owned)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Pack handles this pupdate — e.g. incident → investigate. Job uses the pupdate's repo and title as context.",
    fields: [
      { name: "kind", type: "select", label: "Job kind", default: "investigation", optionsKey: "agent_job_kinds" },
      { name: "ask_first", type: "bool", label: "Ask me before running" },
      { name: "title", type: "string", label: "Title override (optional)", advanced: true },
      { name: "description", type: "textarea", label: "Description override (optional)", rows: 2, advanced: true },
      { name: "specialty_id", type: "select", label: "Specialty", optionsKey: "specialties", advanced: true, help: "Extra context layered onto the agent's role. Silently dropped if the resolved agent doesn't have it attached." },
      { name: "priority", type: "select", label: "Priority", options: PRIORITY_OPTIONS, advanced: true },
    ],
  },
  complete_linked_task: {
    label: "Close the linked task (PR merged / approved)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Closes tasks whose url matches the pupdate's url. Cleans up worktrees for Maiko-owned coding tasks.",
    fields: [],
  },
  skip: {
    label: "Skip it (acknowledge, no action)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Marks the pupdate processed without dispatching anything. Useful for 'ignore this pattern' without deleting the automation.",
    fields: [],
  },
};
