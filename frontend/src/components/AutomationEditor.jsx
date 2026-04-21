import { useEffect, useState } from "react";
import { X, Plus, Trash2, Loader, Save, AlertTriangle } from "lucide-react";
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
  const [scope, setScope] = useState(automation?.execution_scope || "cycle");
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

  // Locking the scope for existing rows — changing it would invalidate
  // every entry in when[]/then[], cleaner to just tell the user they
  // can't flip it and force them to create a new one if they want.
  const scopeLocked = mode === "edit";

  // When the scope toggles on a fresh create, reset when[]/then[] so
  // we don't carry invalid kinds across (e.g. overview_stale isn't
  // valid in pupdate scope).
  useEffect(() => {
    if (!scopeLocked) {
      setWhen([{ kind: "", config: {} }]);
      setThen([{ kind: "", config: {} }]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  const conditionOptions = Object.entries(CONDITION_SCHEMAS)
    .filter(([, s]) => s.scopes.includes(scope))
    .map(([kind, s]) => ({ value: kind, label: s.label }));
  const actionOptions = Object.entries(ACTION_SCHEMAS)
    .filter(([, s]) => s.scopes.includes(scope))
    .map(([kind, s]) => ({ value: kind, label: s.label }));

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

          <div className="automation-editor-field">
            <label>Scope</label>
            <div className="scope-radio-group">
              <label className={`scope-radio ${scope === "cycle" ? "active" : ""} ${scopeLocked ? "locked" : ""}`}>
                <input
                  type="radio"
                  checked={scope === "cycle"}
                  disabled={scopeLocked}
                  onChange={() => setScope("cycle")}
                />
                <span>
                  <strong>Per cycle</strong>
                  <small>Evaluates once per brain tick — used for scheduled runs, stale-state watches, incident correlation.</small>
                </span>
              </label>
              <label className={`scope-radio ${scope === "pupdate" ? "active" : ""} ${scopeLocked ? "locked" : ""}`}>
                <input
                  type="radio"
                  checked={scope === "pupdate"}
                  disabled={scopeLocked}
                  onChange={() => setScope("pupdate")}
                />
                <span>
                  <strong>Per pupdate</strong>
                  <small>Iterates each incoming pupdate, first-match wins. Used for dismiss / create-task / complete-linked-task rules.</small>
                </span>
              </label>
            </div>
            {scopeLocked && (
              <div className="automation-editor-hint"><AlertTriangle size={10} /> Scope is locked for existing automations — create a new one to change it.</div>
            )}
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
              />
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

function ConditionRow({ condition, options, onChange, onRemove }) {
  const schema = CONDITION_SCHEMAS[condition.kind];
  return (
    <div className="automation-entry-row">
      <div className="automation-entry-top">
        <select
          className="automation-entry-kind"
          value={condition.kind}
          onChange={(e) => onChange({ kind: e.target.value, config: defaultConfigFor(CONDITION_SCHEMAS[e.target.value]) })}
        >
          <option value="">Select a trigger…</option>
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {onRemove && (
          <button className="btn-ghost automation-entry-remove" onClick={onRemove} title="Remove">
            <X size={12} />
          </button>
        )}
      </div>
      {schema && <DynamicFields schema={schema} config={condition.config} onChange={(config) => onChange({ config })} />}
    </div>
  );
}

function ActionRow({ action, options, onChange, onRemove }) {
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
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {onRemove && (
          <button className="btn-ghost automation-entry-remove" onClick={onRemove} title="Remove">
            <X size={12} />
          </button>
        )}
      </div>
      {schema && <DynamicFields schema={schema} config={action.config} onChange={(config) => onChange({ config })} />}
    </div>
  );
}


function DynamicFields({ schema, config, onChange }) {
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

  return (
    <div className="automation-entry-fields">
      {schema.help && <div className="automation-entry-help">{schema.help}</div>}
      {schema.fields.map((f) => (
        <FieldInput key={f.name} field={f} value={getField(f.name)} onChange={(v) => setField(f.name, v)} />
      ))}
    </div>
  );
}

function FieldInput({ field, value, onChange }) {
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
    return (
      <label className="automation-field">
        <span>{field.label}{field.help && <small> — {field.help}</small>}</span>
        <select value={v || ""} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">(none)</option>
          {(field.options || []).map((opt) => (
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
  // string default
  return (
    <label className="automation-field">
      <span>{field.label}{field.help && <small> — {field.help}</small>}</span>
      <input
        type="text"
        value={v || ""}
        placeholder={field.placeholder || ""}
        onChange={(e) => onChange(e.target.value)}
      />
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

const TASK_TYPE_OPTIONS = [
  "todo", "bug", "feature", "review", "pr_review",
  "investigation", "repo_analysis", "cartograph", "coding",
];

const CONDITION_SCHEMAS = {
  cadence: {
    label: "On a schedule",
    scopes: ["cycle"],
    help: "Fires every N minutes. Uses last_fired_at + interval as the clock.",
    fields: [
      { name: "interval_minutes", type: "number", label: "Every N minutes", default: 60, min: 1 },
    ],
  },
  overview_stale: {
    label: "Repo overview is stale",
    scopes: ["cycle"],
    help: "Fires when a repo's cartographer insight is missing or older than the threshold.",
    fields: [
      { name: "repo", type: "string", label: "Repo (org/name)", placeholder: "org/repo", help: "required" },
      { name: "stale_days", type: "number", label: "Days before stale", default: 30, min: 1 },
    ],
  },
  lora_missing: {
    label: "Repo has rules but no LoRA adapter",
    scopes: ["cycle"],
    help: "Fires when a repo has N+ active Learnings and no AgentProfile for that scope has an adapter_path set.",
    fields: [
      { name: "repo", type: "string", label: "Repo (org/name)", placeholder: "org/repo" },
      { name: "min_learnings", type: "number", label: "Min active learnings", default: 10, min: 1 },
    ],
  },
  pupdate_chain: {
    label: "Multiple pupdate types within a window",
    scopes: ["cycle"],
    help: "Fires when all the listed pupdate types appear within the time window, grouped by the same key (usually repo).",
    fields: [
      { name: "types", type: "list", label: "Pupdate types", placeholder: "pr_ci_failed, error_spike", help: "comma-separated" },
      { name: "within_minutes", type: "number", label: "Window (minutes)", default: 30, min: 1 },
      { name: "group_by", type: "select", label: "Group by", default: "repo", options: ["repo", "tag"] },
    ],
  },
  pupdate_match: {
    label: "Pupdate matches criteria",
    scopes: ["cycle", "pupdate"],
    help: "Single-pupdate matcher — works in both scopes. In cycle scope it scans recent; in pupdate scope it tests each incoming pupdate.",
    fields: [
      { name: "source", type: "string", label: "Source", placeholder: "github, linear, calendar…" },
      { name: "type", type: "select", label: "Type (exact)", options: PUPDATE_TYPE_OPTIONS },
      { name: "type_prefix", type: "string", label: "Type prefix", placeholder: "pr_" },
      { name: "priority", type: "select", label: "Priority", options: PRIORITY_OPTIONS },
      { name: "actionable", type: "bool", label: "Must be actionable" },
      { name: "has_tag", type: "string", label: "Has tag", placeholder: "e.g. ci" },
      { name: "title_contains", type: "string", label: "Title contains", placeholder: "substring, case-insensitive" },
      { name: "within_minutes", type: "number", label: "Window (cycle scope only)", help: "lookback window; default 60" },
    ],
  },
};

const ACTION_SCHEMAS = {
  propose: {
    label: "Propose a task (user approves)",
    scopes: ["cycle"],
    help: "Emits an agent_proposal pupdate. Approving turns the draft into a routed task.",
    fields: [
      { name: "draft.title", type: "string", label: "Proposal title", placeholder: "Can template {service} etc." },
      { name: "draft.type", type: "select", label: "Task type", default: "todo", options: TASK_TYPE_OPTIONS },
      { name: "draft.priority", type: "select", label: "Priority", default: "normal", options: PRIORITY_OPTIONS },
      { name: "draft.repo", type: "string", label: "Repo", placeholder: "org/repo or {service}" },
      { name: "draft.description", type: "textarea", label: "Description", placeholder: "What the task is. Can template {service}, {types}, {title}." },
    ],
  },
  nudge: {
    label: "Post an inbox nudge (user clicks to act)",
    scopes: ["cycle"],
    help: "Low-priority maiko_nudge pupdate pointing at a URL. No task created.",
    fields: [
      { name: "title", type: "string", label: "Title" },
      { name: "body", type: "textarea", label: "Body", rows: 2 },
      { name: "url", type: "string", label: "URL (relative or absolute)", placeholder: "/knowledge?tab=training" },
      { name: "action_hint", type: "string", label: "Action hint", placeholder: "Open Training" },
    ],
  },
  create_task: {
    label: "Create a task (no approval step)",
    scopes: ["cycle"],
    help: "Skips the propose step — creates a Task directly. Use sparingly; proposals are the safer default.",
    fields: [
      { name: "title", type: "string", label: "Title" },
      { name: "type", type: "select", label: "Task type", default: "todo", options: TASK_TYPE_OPTIONS },
      { name: "priority", type: "select", label: "Priority", default: "normal", options: PRIORITY_OPTIONS },
      { name: "repo", type: "string", label: "Repo", placeholder: "org/repo" },
      { name: "description", type: "textarea", label: "Description" },
    ],
  },
  run_skill: {
    label: "Run a skill as a one-shot task",
    scopes: ["cycle"],
    help: "Creates a task whose `type` is the skill name; the cycle's execute phase runs it as a one-shot.",
    fields: [
      { name: "skill_name", type: "string", label: "Skill name", placeholder: "brainstorm, investigate, morning-brief" },
      { name: "title", type: "string", label: "Task title (optional)" },
      { name: "input", type: "textarea", label: "Skill input (description on the task)", rows: 2 },
      { name: "scope_repo", type: "string", label: "Repo scope", placeholder: "org/repo" },
      { name: "priority", type: "select", label: "Priority", default: "normal", options: PRIORITY_OPTIONS },
    ],
  },
  dismiss_pupdate: {
    label: "Dismiss the matched pupdate",
    scopes: ["pupdate"],
    help: "Archives the pupdate. Pure noise-reduction.",
    fields: [],
  },
  create_task_from_pupdate: {
    label: "Create a task from the matched pupdate",
    scopes: ["pupdate"],
    help: "Uses the pupdate's title/priority as the task seed. Override type and priority here.",
    fields: [
      { name: "task_type", type: "select", label: "Task type", default: "todo", options: TASK_TYPE_OPTIONS },
      { name: "task_priority", type: "select", label: "Task priority", options: PRIORITY_OPTIONS },
    ],
  },
  complete_linked_task: {
    label: "Close the linked review/coding task",
    scopes: ["pupdate"],
    help: "Closes tasks whose url matches the pupdate's url. Cleans up worktrees for Maiko-owned coding tasks.",
    fields: [],
  },
  skip: {
    label: "Skip (no-op, claim the pupdate)",
    scopes: ["pupdate"],
    help: "Marks the pupdate processed without dispatching anything. Useful for 'ignore this pattern' without deleting the automation.",
    fields: [],
  },
};
