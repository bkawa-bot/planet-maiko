import { useEffect, useState } from "react";
import { X, Plus, Trash2, Loader, Save } from "@icons";
import { api } from "../api/client";
import { showToast } from "./Toast";
import "./AutomationEditor.css";
import {
  GroupedOptions,
  ConditionRow,
  ActionRow,
  DynamicFields,
  defaultConfigFor,
} from "./automationFields";
import {
  PUPDATE_TYPE_OPTIONS,
  PRIORITY_OPTIONS,
  TASK_TYPE_OPTIONS,
  CONDITION_SCHEMAS,
  ACTION_SCHEMAS,
} from "./automationSchemas";


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
