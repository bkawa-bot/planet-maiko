import { useEffect, useMemo, useState } from "react";
import { ReactFlow, Background, Controls, MiniMap } from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import "./flow-theme.css";
import { X } from "@icons";
import RoleNode from "./RoleNode";
import GateNode from "./GateNode";
import { useAgentTypes, roleMeta } from "../../../hooks/useAgentTypes";
import { kindColor } from "./kinds";
import { api } from "../../../api/client";
import ModalPortal from "../../ModalPortal";

const nodeTypes = { role: RoleNode, gate: GateNode };
const TERMINAL = new Set(["done", "failed", "partial", "cancelled"]);

// A node's live status, rolled up from its NodeRun(s). 1:1 today; once a
// node fans out it has N instances sharing a node_id, so: running if any
// is running, failed if any failed, done only when all are done.
function rollup(statuses) {
  if (!statuses || !statuses.length) return "pending";
  if (statuses.some((s) => s === "running")) return "running";
  // A gate parked for the user must surface as awaiting, not collapse to
  // pending, or its Approve / Reject buttons never render.
  if (statuses.some((s) => s === "awaiting_approval")) return "awaiting_approval";
  if (statuses.some((s) => s === "queued")) return "queued";
  if (statuses.some((s) => s === "failed")) return "failed";
  if (statuses.every((s) => s === "done")) return "done";
  if (statuses.some((s) => s === "skipped")) return "skipped";
  return "pending";
}

// Read-only live view of a workflow run. Polls the run and paints each
// node by its status (a soft campfire glow, never alarm-red). Reuses
// RoleNode so a run looks like the editor, just lit up.
export default function RunView({ runId, onClose }) {
  const types = useAgentTypes();
  const [run, setRun] = useState(null);
  const [inspect, setInspect] = useState(null);
  const [inspectJob, setInspectJob] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const tick = () => {
      api
        .getWorkflowRun(runId)
        .then((r) => {
          if (cancelled) return;
          setRun(r);
          if (r && TERMINAL.has(r.status) && timer) {
            clearInterval(timer);
            timer = null;
          }
        })
        .catch(() => {});
    };
    tick();
    timer = setInterval(tick, 3000);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [runId]);

  // Fetch the artifact of the node being inspected (its own job, or for
  // a gate, the upstream job it's gating). undefined = loading.
  useEffect(() => {
    if (!inspect || !inspect.jobId) return;
    let cancelled = false;
    api
      .getAgentJob(inspect.jobId)
      .then((j) => { if (!cancelled) setInspectJob(j); })
      .catch(() => { if (!cancelled) setInspectJob(null); });
    return () => { cancelled = true; };
  }, [inspect]);

  const { nodes, edges } = useMemo(() => {
    const g = (run && run.graph_snapshot) || { nodes: [], edges: [] };
    const byNode = {};
    const nrByNode = {};
    for (const nr of (run && run.node_runs) || []) {
      (byNode[nr.node_id] = byNode[nr.node_id] || []).push(nr.status);
      if (!nrByNode[nr.node_id]) nrByNode[nr.node_id] = nr;
    }
    const byId = Object.fromEntries((g.nodes || []).map((n) => [n.id, n]));

    const nodes = (g.nodes || []).map((n) => {
      const status = rollup(byNode[n.id]);
      const ownJobId = nrByNode[n.id] ? nrByNode[n.id].agent_job_id : null;
      if (n.kind === "gate" || n.agent_type === "gate") {
        const nrId = nrByNode[n.id] ? nrByNode[n.id].id : null;
        // What this gate is gating: the artifact of its upstream producer,
        // so clicking the gate shows the plan/diff you're approving.
        const inbound = (g.edges || [])
          .filter((e) => e.target === n.id)
          .map((e) => e.source);
        const upstreamJobId =
          inbound.map((s) => nrByNode[s] && nrByNode[s].agent_job_id).find(Boolean) || ownJobId;
        return {
          id: n.id,
          type: "gate",
          position: { x: n.x ?? 0, y: n.y ?? 0 },
          data: {
            status,
            label: "Approval gate",
            isGate: true,
            jobId: upstreamJobId,
            onApprove: nrId
              ? () => api.approveWorkflowNode(runId, nrId).then(setRun).catch(() => {})
              : null,
            onReject: nrId
              ? () => api.rejectWorkflowNode(runId, nrId).then(setRun).catch(() => {})
              : null,
          },
        };
      }
      const meta = roleMeta(n.agent_type, types);
      return {
        id: n.id,
        type: "role",
        position: { x: n.x ?? 0, y: n.y ?? 0 },
        data: {
          type: meta.raw || {
            id: n.agent_type,
            name: n.agent_type,
            output_kind: "diff",
            input_kind: "task",
            accepts: ["task"],
            icon: "user",
          },
          color: meta.color,
          Icon: meta.icon,
          status,
          label: (meta.raw && meta.raw.name) || n.agent_type,
          jobId: ownJobId,
        },
      };
    });

    const edges = (g.edges || []).map((e) => {
      const src = byId[e.source];
      const kind = src ? roleMeta(src.agent_type, types).outputKind : "diff";
      return {
        id: e.id || `${e.source}__${e.target}`,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle || "out",
        targetHandle: e.targetHandle || "in",
        className: "flow-edge",
        style: { stroke: kindColor(kind) },
      };
    });

    return { nodes, edges };
  }, [run, types, runId]);

  const status = run ? run.status : "loading";
  const allRuns = (run && run.node_runs) || [];
  const doneCount = allRuns.filter((n) => n.status === "done").length;

  // Click any node to read its output. For a gate, that's the upstream
  // artifact it's gating, so you see the plan before approving.
  const openInspect = (node) => {
    const d = node.data || {};
    const jobId = d.jobId || null;
    setInspect({
      jobId,
      label: d.label || node.id,
      isGate: !!d.isGate,
      awaiting: d.status === "awaiting_approval",
      onApprove: d.onApprove,
      onReject: d.onReject,
    });
    // Reset the panel here (event handler), not in the effect, so the
    // fetch effect stays free of synchronous setState. undefined = loading.
    setInspectJob(jobId ? undefined : null);
  };

  return (
    <div className="flow-run-view">
      <div className="flow-editor-bar">
        <span className={`flow-run-status status-${status}`}>{status}</span>
        {allRuns.length > 0 && (
          <span className="flow-run-sub">{doneCount}/{allRuns.length} steps done</span>
        )}
        <span style={{ flex: 1 }} />
        <button className="btn btn-sm" onClick={onClose}>
          <X size={12} /> Close
        </button>
      </div>
      <div className="flow-editor-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          nodesConnectable={false}
          nodesDraggable={false}
          onNodeClick={(_, node) => openInspect(node)}
          minZoom={0.3}
          maxZoom={1.5}
        >
          <Background gap={22} size={1.4} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>

      {inspect && (
        <ModalPortal>
          <div className="modal-overlay" onClick={() => setInspect(null)}>
            <div
              className="agent-edit-modal flow-inspect-modal"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="modal-header">
                <span className="flow-inspect-title">{inspect.label}</span>
                <span style={{ flex: 1 }} />
                <button className="btn btn-sm" onClick={() => setInspect(null)}>
                  <X size={12} />
                </button>
              </div>
              <div className="modal-body flow-inspect-body">
                {inspectJob === undefined ? (
                  <p className="flow-inspect-empty">Loading…</p>
                ) : inspectJob && inspectJob.artifact ? (
                  <pre className="flow-inspect-artifact">{inspectJob.artifact}</pre>
                ) : (
                  <p className="flow-inspect-empty">
                    This step hasn't produced any output yet.
                  </p>
                )}
              </div>
              {inspect.isGate && inspect.awaiting && (
                <div className="agent-edit-footer">
                  <button
                    className="btn"
                    onClick={() => { if (inspect.onReject) inspect.onReject(); setInspect(null); }}
                  >
                    Reject
                  </button>
                  <button
                    className="btn btn-primary"
                    onClick={() => { if (inspect.onApprove) inspect.onApprove(); setInspect(null); }}
                  >
                    Approve
                  </button>
                </div>
              )}
            </div>
          </div>
        </ModalPortal>
      )}
    </div>
  );
}
