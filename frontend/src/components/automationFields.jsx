import { useState } from "react";
import { X } from "@icons";

/**
 * Row + form-builder primitives extracted from AutomationEditor.
 *
 *   GroupedOptions   render <optgroup>s when an option list has groups
 *   ConditionRow     one row in the WHEN list (kind + config)
 *   ActionRow        one row in the THEN list (kind + config)
 *   DynamicFields    walks a schema and renders one input per field
 *   FieldInput       primitive: string / number / bool / textarea / select / list
 *   defaultConfigFor populates a fresh config dict from the schema's defaults
 *
 * Imports CONDITION_SCHEMAS / ACTION_SCHEMAS from automationSchemas
 * so the row components can validate against the right schema based
 * on `kind`.
 */
import { CONDITION_SCHEMAS, ACTION_SCHEMAS } from "./automationSchemas";


export function GroupedOptions({ options }) {
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


export function ConditionRow({ condition, options, pupdateTypes, datalists, optionsMap, onChange, onRemove }) {
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


export function ActionRow({ action, options, datalists, optionsMap, onChange, onRemove }) {
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



export function DynamicFields({ schema, config, datalists, optionsMap, onChange }) {
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



export function defaultConfigFor(schema) {
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