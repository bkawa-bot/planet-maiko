import { useState } from "react";
import { Plus, Pencil, X, Save } from "@icons";
import ModalPortal from "../ModalPortal";
import { showToast } from "../Toast";
import { api } from "../../api/client";
import {
  refreshAgentTypes,
  iconForName,
  AGENT_ICON_CHOICES,
} from "../../hooks/useAgentTypes";

// Editor for a single AgentType (role). `type` null => create mode;
// a type object => edit mode. The four built-ins are editable too;
// the backend flips user_edited=True on any PATCH so a built-in stops
// being refreshed from the bundled spec once you touch it.

const SPAWN_MODES = [
  { value: "worktree", label: "Worktree (real git clone)" },
  { value: "scratch", label: "Scratch (throwaway temp dir)" },
];

const OUTPUT_KINDS = [
  { value: "diff", label: "Diff (review + approve a git diff)" },
  { value: "report", label: "Report (markdown artifact)" },
  { value: "insight", label: "Insight (card for the playbook)" },
  { value: "plan", label: "Plan (an implementation plan for a coder)" },
];

// The IN-side socket vocabulary, mirror of OUTPUT_KINDS. Declares what
// a run hands this role. Shared vocabulary with outputs so a future
// flow editor can type-check an edge (producer.produces feeds
// consumer.accepts). Not consumed at runtime yet; for now it documents
// the role's contract and shows up as the node's input socket.
const INPUT_KINDS = [
  { value: "task", label: "Task (a unit of work)" },
  { value: "plan", label: "Plan (an approved plan)" },
  { value: "diff", label: "Diff (changes to review)" },
  { value: "report", label: "Report (a prior writeup)" },
  { value: "insight", label: "Insight (a repo overview)" },
  { value: "incident", label: "Incident (a failure or alert)" },
  { value: "repo", label: "Repo (a whole repository)" },
];

const PERMISSION_MODES = [
  { value: "", label: "Full access (read + write)" },
  { value: "plan", label: "Plan only (read-only, proposes)" },
];

const PROTOCOL_PLACEHOLDER = `The protocol this role reads at the start of every run, like a
CLAUDE.md just for this kind of agent.

Example:
## Your job
- Triage the failing test in TASK.md
- Find the root cause, don't fix it yet
- Write up what you found as a short report`;

function slugify(s) {
  return (s || "")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

export default function AgentTypeEditorModal({ type, onClose, onSaved }) {
  const isEdit = !!type;
  const isDefault = !!type?.is_default;

  const [form, setForm] = useState(() => ({
    id: type?.id || "",
    name: type?.name || "",
    icon: type?.icon || "user",
    description: type?.description || "",
    protocol_prompt: type?.protocol_prompt || "",
    spawn_mode: type?.spawn_mode || "worktree",
    output_kind: type?.output_kind || "diff",
    input_kind: type?.input_kind || "task",
    accepts: type?.accepts && type.accepts.length ? type.accepts : [type?.input_kind || "task"],
    permission_mode: type?.permission_mode || "",
    model_routing_key: type?.model_routing_key || "coding_agent",
  }));
  // On create, keep the id in lockstep with the name until the user
  // edits the id field directly. On edit the id is the PK and frozen.
  const [idTouched, setIdTouched] = useState(isEdit);
  const [saving, setSaving] = useState(false);

  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  // Toggle an accepted input kind. input_kind tracks the first one as
  // the primary (it drives the lead socket color), so they never drift.
  const toggleAccept = (kind) => {
    setForm((f) => {
      const has = f.accepts.includes(kind);
      const accepts = has ? f.accepts.filter((k) => k !== kind) : [...f.accepts, kind];
      return { ...f, accepts, input_kind: accepts[0] || f.input_kind };
    });
  };

  const onNameChange = (name) => {
    set({ name, ...(!isEdit && !idTouched ? { id: slugify(name) } : {}) });
  };

  const valid =
    form.name.trim() &&
    form.protocol_prompt.trim() &&
    (isEdit || slugify(form.id).length > 0);

  const submit = async () => {
    if (!valid || saving) return;
    setSaving(true);
    try {
      if (isEdit) {
        await api.updateAgentType(type.id, {
          name: form.name.trim(),
          icon: form.icon,
          description: form.description.trim(),
          protocol_prompt: form.protocol_prompt,
          spawn_mode: form.spawn_mode,
          output_kind: form.output_kind,
          input_kind: form.accepts[0] || "task",
          accepts: form.accepts,
          permission_mode: form.permission_mode || null,
          model_routing_key: form.model_routing_key.trim() || "coding_agent",
        });
      } else {
        await api.createAgentType({
          id: slugify(form.id),
          name: form.name.trim(),
          icon: form.icon,
          description: form.description.trim() || undefined,
          protocol_prompt: form.protocol_prompt,
          spawn_mode: form.spawn_mode,
          output_kind: form.output_kind,
          input_kind: form.accepts[0] || "task",
          accepts: form.accepts,
          permission_mode: form.permission_mode || undefined,
          model_routing_key: form.model_routing_key.trim() || "coding_agent",
        });
      }
      await refreshAgentTypes();
      onSaved?.(isEdit ? "updated" : "created");
    } catch (err) {
      // Stay open on error so the user can fix and retry (e.g. a 409
      // when the slug is already taken).
      showToast(err.message || "Save failed", "high");
      setSaving(false);
    }
  };

  return (
    <ModalPortal>
      <div className="modal-overlay" onClick={() => !saving && onClose?.()}>
        <div className="agent-edit-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-header">
            {isEdit ? <Pencil size={14} /> : <Plus size={14} />}
            {isEdit ? `Edit ${type.name}` : "New role"}
            <span style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={onClose} disabled={saving}>
              <X size={12} />
            </button>
          </div>

          <div className="modal-body agent-edit-body agent-type-edit-body">
            {isDefault && (
              <div className="agent-edit-note">
                This is a built-in role. Saving changes keeps your edits
                and stops Maiko from refreshing it on future updates.
              </div>
            )}

            <div className="agent-edit-row">
              <label>
                Name
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => onNameChange(e.target.value)}
                  placeholder="Security Reviewer"
                  autoFocus
                />
              </label>
              <label>
                ID
                {isEdit ? (
                  <span className="agent-type-id-static" title="ID can't change after creation">
                    {form.id}
                  </span>
                ) : (
                  <input
                    type="text"
                    value={form.id}
                    onChange={(e) => { setIdTouched(true); set({ id: e.target.value }); }}
                    onBlur={() => set({ id: slugify(form.id) })}
                    placeholder="security-reviewer"
                  />
                )}
              </label>
            </div>

            <div className="agent-edit-full">
              <div className="agent-edit-label">Icon</div>
              <div className="agent-icon-picker">
                {AGENT_ICON_CHOICES.map((nm) => {
                  const Ico = iconForName(nm);
                  return (
                    <button
                      type="button"
                      key={nm}
                      className={`agent-icon-choice ${form.icon === nm ? "selected" : ""}`}
                      onClick={() => set({ icon: nm })}
                      title={nm}
                    >
                      <Ico size={18} />
                    </button>
                  );
                })}
              </div>
            </div>

            <label className="agent-edit-full">
              Description
              <input
                type="text"
                value={form.description}
                onChange={(e) => set({ description: e.target.value })}
                placeholder="One line on what this role is for."
              />
            </label>

            <div className="agent-edit-full">
              <div className="agent-edit-label">Accepts (input)</div>
              <div className="agent-specialty-grid">
                {INPUT_KINDS.map((o) => {
                  const on = form.accepts.includes(o.value);
                  return (
                    <button
                      type="button"
                      key={o.value}
                      className={`agent-specialty-chip ${on ? "checked" : ""}`}
                      onClick={() => toggleAccept(o.value)}
                      title={o.label}
                    >
                      {o.value}
                    </button>
                  );
                })}
              </div>
            </div>

            <label className="agent-edit-full">
              Produces (output)
              <select value={form.output_kind} onChange={(e) => set({ output_kind: e.target.value })}>
                {OUTPUT_KINDS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <span className="agent-edit-hint">
              The role's typed sockets. A flow wires this role's output into any role
              whose Accepts list includes that kind, so a Planner that produces a plan
              can feed a Coder that accepts plans. Pick every input this role can run from.
            </span>

            <div className="agent-edit-row">
              <label>
                Workspace
                <select value={form.spawn_mode} onChange={(e) => set({ spawn_mode: e.target.value })}>
                  {SPAWN_MODES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label>
                Permissions
                <select value={form.permission_mode} onChange={(e) => set({ permission_mode: e.target.value })}>
                  {PERMISSION_MODES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
            </div>

            <label className="agent-edit-full">
              Model routing key
              <input
                type="text"
                value={form.model_routing_key}
                onChange={(e) => set({ model_routing_key: e.target.value })}
                placeholder="coding_agent"
              />
            </label>
            <span className="agent-edit-hint">
              The routing key picks this role's model + effort. It must match a
              rule in Settings, Model Routing, otherwise it falls back to the
              coding default.
            </span>

            <label className="agent-edit-full">
              Protocol
              <textarea
                rows={12}
                value={form.protocol_prompt}
                onChange={(e) => set({ protocol_prompt: e.target.value })}
                placeholder={PROTOCOL_PLACEHOLDER}
              />
            </label>
          </div>

          <div className="agent-edit-footer">
            <button className="btn" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={submit} disabled={saving || !valid}>
              {saving
                ? "Saving..."
                : isEdit
                ? <><Save size={12} /> Save</>
                : <><Plus size={12} /> Create role</>}
            </button>
          </div>
        </div>
      </div>
    </ModalPortal>
  );
}
