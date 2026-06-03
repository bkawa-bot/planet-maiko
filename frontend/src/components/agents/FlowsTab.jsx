import { useEffect, useState } from "react";
import { Plus, Trash2 } from "@icons";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import { useAgentTypes } from "../../hooks/useAgentTypes";
import { relativeTime } from "../../utils/dates";
import ConfirmModal from "../ConfirmModal";
import FlowEditor from "./flow/FlowEditor";
import RunView from "./flow/RunView";

// The Flows surface: a list of saved flows + the editor. `editing` is
// the view switch — undefined shows the list, null opens a fresh flow,
// an object opens that saved flow. Running flows is a later phase; this
// tab is author + save only.
export default function FlowsTab() {
  const types = useAgentTypes();
  const [flows, setFlows] = useState([]);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(undefined);
  const [viewingRun, setViewingRun] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const load = () => {
    api
      .getWorkflows()
      .then((f) => setFlows(Array.isArray(f) ? f : []))
      .catch(() => setFlows([]))
      .finally(() => setLoading(false));
    api
      .getWorkflowRuns()
      .then((r) => setRuns(Array.isArray(r) ? r : []))
      .catch(() => {});
  };

  // Initial fetch + a light poll of runs so the list reflects live
  // status and a running flow stays reachable after you navigate away.
  // setState happens only in promise callbacks (never synchronously in
  // the effect body), guarded by a cancel flag.
  useEffect(() => {
    let cancelled = false;
    api
      .getWorkflows()
      .then((f) => { if (!cancelled) setFlows(Array.isArray(f) ? f : []); })
      .catch(() => { if (!cancelled) setFlows([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    const loadRuns = () =>
      api
        .getWorkflowRuns()
        .then((r) => { if (!cancelled) setRuns(Array.isArray(r) ? r : []); })
        .catch(() => {});
    loadRuns();
    const timer = setInterval(loadRuns, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  if (viewingRun) {
    return (
      <RunView
        runId={viewingRun}
        onClose={() => { setViewingRun(null); setEditing(undefined); load(); }}
      />
    );
  }

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
        onRan={(runRes) => { setEditing(undefined); setViewingRun(runRes.id); }}
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

  const handleStopRun = async (id) => {
    try {
      await api.stopWorkflowRun(id);
      showToast("Run stopped", "normal");
      load();
    } catch (err) {
      showToast(err.message || "Stop failed", "high");
    }
  };

  const handleDeleteRun = async (id) => {
    try {
      await api.deleteWorkflowRun(id);
      showToast("Run removed", "normal");
      load();
    } catch (err) {
      showToast(err.message || "Delete failed", "high");
    }
  };

  const handleStopAll = async () => {
    const running = runs.filter((r) => r.status === "running");
    if (running.length === 0) return;
    await Promise.allSettled(running.map((r) => api.stopWorkflowRun(r.id)));
    showToast(`Stopped ${running.length} run${running.length === 1 ? "" : "s"}`, "normal");
    load();
  };

  const hasTrigger = (f) =>
    !!(f.graph && f.graph.nodes && f.graph.nodes.some((n) => n.kind === "trigger"));

  const handleArm = async (f) => {
    try {
      await api.armWorkflow(f.id, !f.trigger_armed);
      showToast(f.trigger_armed ? "Flow paused" : "Flow armed 🐾", "normal");
      load();
    } catch (err) {
      showToast(err.message || "Toggle failed", "high");
    }
  };

  if (loading) return <p className="page-empty">Loading flows…</p>;

  return (
    <div className="flows-tab">
      <div className="profiles-toolbar">
        <button className="btn btn-primary" onClick={() => setEditing(null)}>
          <Plus size={12} /> New flow
        </button>
      </div>

      {runs.length > 0 && (
        <div className="flow-runs-section">
          <div className="flow-runs-header">
            <span className="flow-runs-label">Recent runs</span>
            {runs.some((r) => r.status === "running") && (
              <button className="btn btn-sm" onClick={handleStopAll} title="Stop every running flow">
                Stop all running
              </button>
            )}
          </div>
          <div className="flow-runs-list">
            {runs.slice(0, 10).map((r) => (
              <div key={r.id} className="flow-run-row">
                <button
                  className="flow-run-row-main"
                  onClick={() => setViewingRun(r.id)}
                >
                  <span className={`flow-run-row-status status-${r.status}`}>{r.status}</span>
                  <span className="flow-run-row-name">{r.workflow_name}</span>
                  <span className={`flow-run-row-meta${r.awaiting > 0 ? " awaiting" : ""}`}>
                    {r.awaiting > 0
                      ? `${r.awaiting} awaiting approval`
                      : `${r.steps_done}/${r.steps_total} steps`}
                    {" · "}
                    {relativeTime(r.created_at)}
                  </span>
                </button>
                <div className="flow-run-row-actions">
                  {r.status === "running" && (
                    <button
                      className="btn btn-sm"
                      onClick={() => handleStopRun(r.id)}
                      title="Stop this run"
                    >
                      Stop
                    </button>
                  )}
                  <button
                    className="btn btn-sm flow-card-delete"
                    onClick={() => handleDeleteRun(r.id)}
                    title="Remove this run"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

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
                  {hasTrigger(f) && (
                    <button
                      className={`btn btn-sm flow-arm-btn${f.trigger_armed ? " armed" : ""}`}
                      onClick={() => handleArm(f)}
                      title={f.trigger_armed
                        ? "Armed — firing on matching pupdates. Click to pause."
                        : "Paused. Click to arm (go live)."}
                    >
                      {f.trigger_armed ? "● Armed" : "Paused"}
                    </button>
                  )}
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
