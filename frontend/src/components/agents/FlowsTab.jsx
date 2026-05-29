import { useEffect, useState } from "react";
import { Plus, Trash2 } from "@icons";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import { useAgentTypes } from "../../hooks/useAgentTypes";
import { relativeTime } from "../../utils/dates";
import ConfirmModal from "../ConfirmModal";
import FlowEditor from "./flow/FlowEditor";

// The Flows surface: a list of saved flows + the editor. `editing` is
// the view switch — undefined shows the list, null opens a fresh flow,
// an object opens that saved flow. Running flows is a later phase; this
// tab is author + save only.
export default function FlowsTab() {
  const types = useAgentTypes();
  const [flows, setFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(undefined);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const load = () => {
    api
      .getWorkflows()
      .then((f) => setFlows(Array.isArray(f) ? f : []))
      .catch(() => setFlows([]))
      .finally(() => setLoading(false));
  };

  // Initial fetch. setState happens only in the promise callbacks
  // (never synchronously in the effect body), guarded by a cancel flag.
  useEffect(() => {
    let cancelled = false;
    api
      .getWorkflows()
      .then((f) => { if (!cancelled) setFlows(Array.isArray(f) ? f : []); })
      .catch(() => { if (!cancelled) setFlows([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (editing !== undefined) {
    return (
      <FlowEditor
        workflow={editing}
        types={types}
        onClose={() => setEditing(undefined)}
        onSaved={() => {
          setEditing(undefined);
          load();
          showToast("Flow saved 🐾", "normal");
        }}
      />
    );
  }

  const handleDelete = async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    try {
      await api.deleteWorkflow(deleteTarget.id);
      showToast(`Removed ${deleteTarget.name}`, "normal");
      setDeleteTarget(null);
      load();
    } catch (err) {
      showToast(err.message || "Delete failed", "high");
    }
    setDeleting(false);
  };

  if (loading) return <p className="page-empty">Loading flows…</p>;

  return (
    <div className="flows-tab">
      <div className="profiles-toolbar">
        <button className="btn btn-primary" onClick={() => setEditing(null)}>
          <Plus size={12} /> New flow
        </button>
      </div>

      {flows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-title">No flows yet</div>
          <div className="empty-sub">
            Build a pipeline by wiring roles together. One role's output drops
            into the next role's input.
          </div>
        </div>
      ) : (
        <div className="flows-grid">
          {flows.map((f) => {
            const steps = (f.graph && f.graph.nodes ? f.graph.nodes.length : 0);
            return (
              <div key={f.id} className="flow-card">
                <button className="flow-card-main" onClick={() => setEditing(f)}>
                  <div className="flow-card-name">{f.name}</div>
                  {f.description && <div className="flow-card-desc">{f.description}</div>}
                  <div className="flow-card-meta">
                    {steps} {steps === 1 ? "step" : "steps"} · edited {relativeTime(f.updated_at)}
                  </div>
                </button>
                <div className="flow-card-actions">
                  <button className="btn btn-sm" onClick={() => setEditing(f)}>Open</button>
                  <button
                    className="btn btn-sm flow-card-delete"
                    onClick={() => setDeleteTarget(f)}
                    title="Delete flow"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <ConfirmModal
        open={!!deleteTarget}
        severity="danger"
        title={`Delete "${deleteTarget?.name}"?`}
        body="This removes the saved flow. The roles it used are untouched."
        confirmText="Delete it"
        busy={deleting}
        onConfirm={handleDelete}
        onCancel={() => !deleting && setDeleteTarget(null)}
      />
    </div>
  );
}
