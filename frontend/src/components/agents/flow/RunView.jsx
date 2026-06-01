import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
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
  const navigate = useNavigate();
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
    const instancesByNode = {};
    for (const nr of (run && run.node_runs) || []) {
      (byNode[nr.node_id] = byNode[nr.node_id] || []).push(nr.status);
      if (!nrByNode[nr.node_id]) nrByNode[nr.node_id] = nr;
      (instancesByNode[nr.node_id] = instancesByNode[nr.node_id] || []).push(nr);
    }
    // Stable order for a node's scatter instances, by instance index.
    for (const k in instancesByNode) {
      instancesByNode[k].sort(
        (a, b) => ((a.extra && a.extra.instance) || 0) - ((b.extra && b.extra.instance) || 0)
      );
    }
    const byId = Object.fromEntries((g.nodes || []).map((n) => [n.id, n]));

    const fallbackType = (n) => ({
      id: n.agent_type,
      name: n.agent_type,
      output_kind: "diff",
      input_kind: "task",
      accepts: ["task"],
      icon: "user",
    });

    const nodes = (g.nodes || []).flatMap((n) => {
      const isGate = n.kind === "gate" || n.agent_type === "gate";
      if (isGate) {
        const status = rollup(byNode[n.id]);
        const nr = nrByNode[n.id];
        const nrId = nr ? nr.id : null;
        const ownJobId = nr ? nr.agent_job_id : null;
        // What this gate is gating: the artifact of its upstream producer,
        // so clicking the gate shows the plan/diff you're approving.
        const inbound = (g.edges || [])
          .filter((e) => e.target === n.id)
          .map((e) => e.source);
        const upstreamJobId =
          inbound.map((s) => nrByNode[s] && nrByNode[s].agent_job_id).find(Boolean) || ownJobId;
        return [{
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
        }];
      }

      const meta = roleMeta(n.agent_type, types);
      const roleData = (status, jobId, sub, avatar, statusLine) => ({
        type: meta.raw || fallbackType(n),
        color: meta.color,
        Icon: meta.icon,
        status,
        label: (meta.raw && meta.raw.name) || n.agent_type,
        sub,
        jobId,
        avatar,
        statusLine,
      });

      const insts = instancesByNode[n.id] || [];
      if (insts.length > 1) {
        // SCATTER: one node per instance, cascaded down-right from the
        // graph slot so the fan reads as "these came from here."
        return insts.map((inst, i) => {
          const lbl = (inst.extra && inst.extra.label) || `task ${i + 1}`;
          const rnd = inst.extra && inst.extra.round;
          return {
            id: `${n.id}__${inst.id}`,
            type: "role",
            position: { x: (n.x ?? 0) + i * 28, y: (n.y ?? 0) + i * 108 },
            data: roleData(inst.status, inst.agent_job_id, rnd ? `${lbl} · round ${rnd}` : lbl, inst.agent_avatar, inst.agent_status),
          };
        });
      }

      // Single instance (or none yet): one node in the graph slot. A
      // reviewer-loop revision round shows as a small "round N" label.
      const status = rollup(byNode[n.id]);
      const only = nrByNode[n.id];
      const ownJobId = only ? only.agent_job_id : null;
      const rnd = only && only.extra ? only.extra.round : 0;
      return [{
        id: n.id,
        type: "role",
        position: { x: n.x ?? 0, y: n.y ?? 0 },
        data: roleData(status, ownJobId, rnd ? `revision round ${rnd}` : null, only && only.agent_avatar, only && only.agent_status),
      }];
    });

    // A scattered node renders as N instance nodes, so an edge touching it
    // fans to every instance (source x target cross-product). For 1:1
    // nodes this collapses to the single original edge.
    const rfIds = (graphNodeId) => {
      const gn = byId[graphNodeId];
      const isGate = gn && (gn.kind === "gate" || gn.agent_type === "gate");
      const insts = instancesByNode[graphNodeId] || [];
      if (!isGate && insts.length > 1) {
        return insts.map((inst) => `${graphNodeId}__${inst.id}`);
      }
      return [graphNodeId];
    };

    const edges = (g.edges || []).flatMap((e) => {
      const src = byId[e.source];
      const kind = src ? roleMeta(src.agent_type, types).outputKind : "diff";
      const mk = (s, t) => ({
        id: `${s}__${t}`,
        source: s,
        target: t,
        sourceHandle: e.sourceHandle || "out",
        targetHandle: e.targetHandle || "in",
        className: "flow-edge",
        style: { stroke: kindColor(kind) },
      });
      // Paired fan-in: when every target instance is paired 1:1 to an
      // upstream instance, draw coder_i -> reviewer_i, not a full mesh.
      const tInsts = instancesByNode[e.target] || [];
      const paired = tInsts.length > 1 && tInsts.every((ti) => ti.extra && ti.extra.paired_to);
      if (paired) {
        return tInsts.map((ti) => mk(`${e.source}__${ti.extra.paired_to}`, `${e.target}__${ti.id}`));
      }
      const out = [];
      for (const s of rfIds(e.source)) {
        for (const t of rfIds(e.target)) out.push(mk(s, t));
      }
      return out;
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
      label: d.sub ? `${d.label || ""}: ${d.sub}` : (d.label || node.id),
      isGate: !!d.isGate,
      status: d.status,
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
                {inspect.jobId && (
                  <button
                    className="btn btn-sm"
                    onClick={() => navigate(`/jobs/${inspect.jobId}`)}
                    title="Open this step's full page (diff, chat, report) to review and PR"
                  >
                    Open full page
                  </button>
                )}
                <button className="btn btn-sm" onClick={() => setInspect(null)}>
                  <X size={12} />
                </button>
              </div>
              <div className="modal-body flow-inspect-body">
                {inspectJob === undefined ? (
                  <p className="flow-inspect-empty">Loading…</p>
                ) : inspectJob && inspectJob.artifact ? (
                  <pre className="flow-inspect-artifact">{inspectJob.artifact}</pre>
                ) : inspect.status === "running" || inspect.status === "queued" ? (
                  <p className="flow-inspect-empty">
                    This step is still working. Open its full page to watch it live.
                  </p>
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
