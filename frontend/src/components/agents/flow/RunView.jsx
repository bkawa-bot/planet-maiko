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
      if (n.kind === "gate" || n.agent_type === "gate") {
        const nrId = nrByNode[n.id] ? nrByNode[n.id].id : null;
        return {
          id: n.id,
          type: "gate",
          position: { x: n.x ?? 0, y: n.y ?? 0 },
          data: {
            status,
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
          minZoom={0.3}
          maxZoom={1.5}
        >
          <Background gap={22} size={1.4} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
    </div>
  );
}
