import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { GitBranch, X, Play } from "@icons";
import ModalPortal from "./ModalPortal";

// Launch a saved flow on an existing task: pick a flow, optionally tweak the
// kickoff input + repo (both prefilled from the task), and run it. The run is
// linked back to the task (which goes in_progress) and shows up under
// Workshop > Flows > Recent runs.
export default function RunFlowModal({ task, onClose, onLaunched }) {
  const [flows, setFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(null);
  const [input, setInput] = useState("");
  const [repo, setRepo] = useState("");
  const [running, setRunning] = useState(false);

  useEffect(() => {
    const desc = task.metadata?.description || "";
    setInput(`${task.title}${desc ? `\n\n${desc}` : ""}`);
    setRepo(task.metadata?.repo || "");
    api
      .getWorkflows()
      .then((f) => setFlows(Array.isArray(f) ? f : []))
      .catch(() => setFlows([]))
      .finally(() => setLoading(false));
  }, [task]);

  const run = async () => {
    if (running || !selectedId) return;
    setRunning(true);
    try {
      const res = await api.runWorkflow(selectedId, {
        task_id: task.id,
        input: input.trim() || undefined,
        scope_repo: repo.trim() || undefined,
      });
      showToast("Flow launched 🐾 — see Workshop › Flows", "normal");
      onLaunched?.(res);
      onClose?.();
    } catch (err) {
      showToast(err.message || "Couldn't start the flow", "high");
      setRunning(false);
    }
  };

  return (
    <ModalPortal>
      <div className="modal-overlay" onClick={() => !running && onClose?.()}>
        <div className="agent-edit-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-header">
            <GitBranch size={14} /> Run a flow
            <span style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={onClose} disabled={running}>
              <X size={12} />
            </button>
          </div>

          <div className="modal-body agent-edit-body">
            <div className="agent-edit-hint" style={{ marginBottom: 8 }}>
              On task: <strong>{task.title}</strong>
            </div>

            {loading ? (
              <p className="page-empty">Loading flows…</p>
            ) : flows.length === 0 ? (
              <div className="agent-edit-hint">
                No saved flows yet. Build one in Workshop › Flows first.
              </div>
            ) : (
              <>
                <div className="assign-section-label">Pick a flow</div>
                <div className="assign-agent-list">
                  {flows.map((f) => {
                    const steps = f.graph?.nodes?.length || 0;
                    return (
                      <div
                        key={f.id}
                        className={`assign-agent-option ${selectedId === f.id ? "selected" : ""}`}
                        onClick={() => setSelectedId(f.id)}
                      >
                        <div className="assign-agent-info">
                          <div className="assign-agent-name">
                            <GitBranch size={12} /> {f.name}
                          </div>
                          {f.description && (
                            <div className="assign-agent-reasons">{f.description}</div>
                          )}
                          <div className="assign-agent-reasons">
                            {steps} {steps === 1 ? "step" : "steps"}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <label className="agent-edit-full" style={{ marginTop: 12 }}>
                  Kickoff input (seeds the first step)
                  <textarea rows={4} value={input} onChange={(e) => setInput(e.target.value)} />
                </label>
                <label className="agent-edit-full">
                  Repo (optional)
                  <input
                    type="text"
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                    placeholder="org/repo"
                  />
                </label>
              </>
            )}
          </div>

          <div className="agent-edit-footer">
            <button className="btn" onClick={onClose} disabled={running}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              onClick={run}
              disabled={running || !selectedId}
            >
              {running ? "Starting..." : <><Play size={12} /> Run flow</>}
            </button>
          </div>
        </div>
      </div>
    </ModalPortal>
  );
}
