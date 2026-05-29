import { useState } from "react";
import { Plus, Pencil, Trash2 } from "@icons";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import { useAgentTypes, refreshAgentTypes, roleMeta } from "../../hooks/useAgentTypes";
import ConfirmModal from "../ConfirmModal";
import AgentTypeEditorModal from "./AgentTypeEditorModal";

// Management surface for AgentTypes (roles). Lists built-ins + custom
// types, opens the editor for create / edit, and deletes (custom rows
// hard-delete; built-ins soft-delete behind a tombstone). All mutations
// route through refreshAgentTypes() so the New Agent role picker and
// every roleMeta() consumer stay in sync without a reload.
export default function AgentTypesTab() {
  const types = useAgentTypes();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    try {
      await api.deleteAgentType(deleteTarget.id);
      await refreshAgentTypes();
      showToast(`Removed ${deleteTarget.name}`, "normal");
      setDeleteTarget(null);
    } catch (err) {
      showToast(err.message || "Delete failed", "high");
    }
    setDeleting(false);
  };

  return (
    <div className="agent-types-tab">
      <div className="profiles-toolbar">
        <button className="btn btn-primary" onClick={() => setCreating(true)}>
          <Plus size={12} /> New role
        </button>
      </div>

      {types.length === 0 ? (
        <p className="page-empty">No roles yet.</p>
      ) : (
        <div className="agent-types-grid">
          {types.map((t) => {
            const meta = roleMeta(t.id, types);
            const Icon = meta.icon;
            return (
              <div key={t.id} className="agent-type-card">
                <button className="agent-type-card-main" onClick={() => setEditing(t)}>
                  <span
                    className="agent-type-icon"
                    style={{
                      color: meta.color,
                      borderColor: meta.color,
                      background: `color-mix(in srgb, ${meta.color} 12%, transparent)`,
                    }}
                  >
                    <Icon size={22} />
                  </span>
                  <span className="agent-type-body">
                    <span className="agent-type-name-row">
                      <span className="agent-type-name">{t.name}</span>
                      <span className={`agent-type-badge ${t.is_default ? "default" : "custom"}`}>
                        {t.is_default ? "Built-in" : "Custom"}
                      </span>
                    </span>
                    {t.description && <span className="agent-type-desc">{t.description}</span>}
                    <span className="agent-type-chips">
                      <span className="agent-type-chip io">{t.input_kind || "task"} → {t.output_kind}</span>
                      <span className="agent-type-chip">{t.spawn_mode}</span>
                      {t.permission_mode === "plan" && (
                        <span className="agent-type-chip plan">plan mode</span>
                      )}
                      <span className="agent-type-chip mono">{t.model_routing_key}</span>
                    </span>
                  </span>
                </button>
                <div className="agent-type-actions">
                  <button className="btn btn-sm" onClick={() => setEditing(t)}>
                    <Pencil size={12} /> Edit
                  </button>
                  <button
                    className="btn btn-sm agent-type-delete-btn"
                    onClick={() => setDeleteTarget(t)}
                    title={t.is_default ? "Hide built-in role" : "Delete role"}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {(creating || editing) && (
        <AgentTypeEditorModal
          type={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={(action) => {
            setCreating(false);
            setEditing(null);
            showToast(action === "created" ? "New role added 🐾" : "Role updated", "normal");
          }}
        />
      )}

      <ConfirmModal
        open={!!deleteTarget}
        severity="danger"
        title={
          deleteTarget?.is_default
            ? `Hide built-in role "${deleteTarget?.name}"?`
            : `Delete "${deleteTarget?.name}"?`
        }
        body={
          deleteTarget?.is_default
            ? "It won't be offered for new agents and won't come back on restart. Agents already using it fall back to Coder."
            : "This removes the custom role for good. Agents already using it fall back to Coder."
        }
        confirmText={deleteTarget?.is_default ? "Hide it" : "Delete it"}
        busy={deleting}
        onConfirm={handleDelete}
        onCancel={() => !deleting && setDeleteTarget(null)}
      />
    </div>
  );
}
