import { useCallback, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import "./flow-theme.css";
import { X, Save, Play, CombinationLock } from "@icons";
import RoleNode from "./RoleNode";
import GateNode from "./GateNode";
import { roleMeta } from "../../../hooks/useAgentTypes";
import { kindColor, edgeValid } from "./kinds";
import { api } from "../../../api/client";
import { showToast } from "../../Toast";
import ModalPortal from "../../ModalPortal";

const nodeTypes = { role: RoleNode, gate: GateNode };

// Placeholder for the Run dialog's kickoff input, keyed by what the first
// step accepts. The label itself is the capitalized kind.
const KICKOFF_HINT = {
  task: "What should this flow do? Handed to the first step as its task.\n\nExample: Add rate limiting to the /login endpoint.",
  plan: "Paste the plan for the first step to work from (a decomposer breaks it into tasks).",
  incident: "Describe the incident or failure the first step should dig into.",
  repo: "What should the first step map or analyze?",
  report: "Paste the report the first step should work from.",
  diff: "Describe the change the first step should review.",
};

// Hydrate the saved graph blob into React Flow nodes/edges. A node
// references a role by agent_type; we re-resolve its live color + icon
// from roleMeta so a role re-skin shows up without re-saving the flow.
function buildInitial(workflow, types) {
  const g = (workflow && workflow.graph) || { nodes: [], edges: [] };
  const list = types || [];
  const byId = Object.fromEntries(list.map((t) => [t.id, t]));

  const nodes = (g.nodes || []).map((n) => {
    if (n.kind === "gate" || n.agent_type === "gate") {
      return {
        id: n.id,
        type: "gate",
        position: { x: n.x ?? 0, y: n.y ?? 0 },
        data: { editable: true },
      };
    }
    const t = byId[n.agent_type] || {
      id: n.agent_type,
      name: n.agent_type,
      input_kind: "task",
      output_kind: "diff",
      icon: "user",
    };
    const meta = roleMeta(n.agent_type, list);
    return {
      id: n.id,
      type: "role",
      position: { x: n.x ?? 0, y: n.y ?? 0 },
      data: { type: t, color: meta.color, Icon: meta.icon, editable: true },
    };
  });

  const edges = (g.edges || []).map((e) => {
    const srcNode = (g.nodes || []).find((n) => n.id === e.source);
    const srcType = srcNode ? byId[srcNode.agent_type] : null;
    const kind = (srcType && srcType.output_kind) || "diff";
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
}

// The editable flow canvas. Drag roles in from the palette, wire output
// doorways to matching input doorways (the connection is rejected unless
// the kinds match), and save the whole canvas as one graph blob. No
// execution yet; this is Phase C (authoring), the engine is Phase D.
export default function FlowEditor({ workflow, types, onSaved, onClose, onRan }) {
  const initial = useMemo(() => buildInitial(workflow, types), [workflow, types]);
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges);
  const [name, setName] = useState((workflow && workflow.name) || "Untitled flow");
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [showRun, setShowRun] = useState(false);
  const [runTask, setRunTask] = useState("");
  const [scopeRepo, setScopeRepo] = useState("");
  const [rf, setRf] = useState(null);

  // The Run input kicks off the FIRST step(s), so label it by what they
  // accept (a decomposer wants a "Plan", an investigator an "Incident", a
  // coder a "Task"). Roots = nodes with no inbound edge; if they disagree,
  // fall back to the generic "task".
  const kickoffKind = useMemo(() => {
    const targets = new Set(edges.map((e) => e.target));
    const roots = nodes.filter((n) => n.type !== "gate" && !targets.has(n.id));
    const kinds = roots.map((n) => {
      const t = n.data.type || {};
      const accepts = (t.accepts && t.accepts.length) ? t.accepts : [t.input_kind || "task"];
      return accepts[0] || "task";
    });
    return kinds.length && kinds.every((k) => k === kinds[0]) ? kinds[0] : "task";
  }, [nodes, edges]);

  // A wire is valid only when the producer's output kind equals the
  // consumer's input kind. React Flow calls this during a drag and
  // refuses the drop on false.
  const isValidConnection = useCallback(
    (conn) => {
      const src = nodes.find((n) => n.id === conn.source);
      const tgt = nodes.find((n) => n.id === conn.target);
      if (!src || !tgt) return false;
      // A gate is a pass-through; any wire to or from it is valid.
      if (src.type === "gate" || tgt.type === "gate") return true;
      const out = src.data.type.output_kind || "diff";
      const t = tgt.data.type;
      const accepts = (t.accepts && t.accepts.length) ? t.accepts : [t.input_kind || "task"];
      return edgeValid(out, accepts);
    },
    [nodes]
  );

  const onConnect = useCallback(
    (conn) => {
      const src = nodes.find((n) => n.id === conn.source);
      const kind = (src && src.type !== "gate" && src.data.type.output_kind) || "diff";
      setEdges((eds) =>
        addEdge(
          { ...conn, className: "flow-edge", style: { stroke: kindColor(kind) } },
          eds
        )
      );
    },
    [nodes, setEdges]
  );

  const addRole = (role) => {
    const meta = roleMeta(role.id, types);
    const id = `${role.id}-${crypto.randomUUID().slice(0, 8)}`;
    const offset = nodes.length;
    setNodes((nds) =>
      nds.concat({
        id,
        type: "role",
        position: { x: 140 + (offset % 4) * 50, y: 90 + (offset % 6) * 46 },
        data: { type: role, color: meta.color, Icon: meta.icon, editable: true },
      })
    );
  };

  const addGate = () => {
    const id = `gate-${crypto.randomUUID().slice(0, 8)}`;
    const offset = nodes.length;
    setNodes((nds) =>
      nds.concat({
        id,
        type: "gate",
        position: { x: 140 + (offset % 4) * 50, y: 90 + (offset % 6) * 46 },
        data: { editable: true },
      })
    );
  };

  const serializeGraph = () => ({
    nodes: nodes.map((n) => ({
      id: n.id,
      kind: n.type === "gate" ? "gate" : "role",
      agent_type: n.type === "gate" ? "gate" : n.data.type.id,
      x: Math.round(n.position.x),
      y: Math.round(n.position.y),
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle,
      targetHandle: e.targetHandle,
    })),
    viewport: rf ? rf.getViewport() : undefined,
  });

  const persist = async () => {
    const body = { name: name.trim() || "Untitled flow", graph: serializeGraph() };
    return workflow && workflow.id
      ? api.updateWorkflow(workflow.id, body)
      : api.createWorkflow(body);
  };

  const save = async () => {
    if (saving || running) return;
    setSaving(true);
    try {
      const saved = await persist();
      onSaved?.(saved);
    } catch (err) {
      showToast(err.message || "Save failed", "high");
      setSaving(false);
    }
  };

  const doRun = async () => {
    if (saving || running || !runTask.trim()) return;
    setRunning(true);
    try {
      // Save the current canvas first so the run executes what's on screen,
      // then launch it with the kickoff task as the flow's input.
      const saved = await persist();
      const runRes = await api.runWorkflow(saved.id, {
        input: runTask.trim(),
        scope_repo: scopeRepo.trim() || undefined,
      });
      showToast("Flow is running 🐾", "normal");
      onRan?.(runRes);
    } catch (err) {
      showToast(err.message || "Couldn't start the run", "high");
      setRunning(false);
    }
  };

  return (
    <div className="flow-editor">
      <div className="flow-editor-bar">
        <input
          className="flow-editor-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Flow name"
        />
        <span style={{ flex: 1 }} />
        <button className="btn btn-sm" onClick={onClose} disabled={saving || running}>
          <X size={12} /> Close
        </button>
        <button className="btn btn-sm" onClick={save} disabled={saving || running}>
          {saving ? "Saving..." : <><Save size={12} /> Save</>}
        </button>
        <button
          className="btn btn-primary btn-sm"
          onClick={() => setShowRun(true)}
          disabled={saving || running || nodes.length === 0}
        >
          <Play size={12} /> Run
        </button>
      </div>

      <div className="flow-editor-body">
        <div className="flow-palette">
          <div className="flow-palette-label">Bring in a role</div>
          {(types || []).map((t) => {
            const meta = roleMeta(t.id, types);
            const Icon = meta.icon;
            return (
              <button
                key={t.id}
                className="flow-palette-item"
                onClick={() => addRole(t)}
                title={`accepts ${t.input_kind || "task"}, produces ${t.output_kind}`}
              >
                <span className="flow-palette-icon" style={{ color: meta.color }}>
                  {Icon ? <Icon size={16} /> : null}
                </span>
                <span className="flow-palette-name">{t.name}</span>
                <span className="flow-palette-io">
                  {(t.input_kind || "task")}→{t.output_kind}
                </span>
              </button>
            );
          })}
          <div className="flow-palette-label flow-palette-control">Control</div>
          <button
            className="flow-palette-item"
            onClick={addGate}
            title="Pause the run here for your approval"
          >
            <span className="flow-palette-icon"><CombinationLock size={16} /></span>
            <span className="flow-palette-name">Approval gate</span>
          </button>
        </div>

        <div className="flow-editor-canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            nodeTypes={nodeTypes}
            onInit={setRf}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.3}
            maxZoom={1.5}
            deleteKeyCode={["Backspace", "Delete"]}
          >
            <Background gap={22} size={1.4} />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable />
          </ReactFlow>
          {nodes.length === 0 && (
            <div className="flow-empty">Click a role on the left to drop it in.</div>
          )}
        </div>
      </div>

      {showRun && (
        <ModalPortal>
          <div className="modal-overlay" onClick={() => !running && setShowRun(false)}>
            <div className="agent-edit-modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <Play size={14} /> Run flow
                <span style={{ flex: 1 }} />
                <button className="btn btn-sm" onClick={() => setShowRun(false)} disabled={running}>
                  <X size={12} />
                </button>
              </div>
              <div className="modal-body agent-edit-body">
                <label className="agent-edit-full">
                  {kickoffKind.charAt(0).toUpperCase() + kickoffKind.slice(1)}
                  <textarea
                    rows={5}
                    value={runTask}
                    onChange={(e) => setRunTask(e.target.value)}
                    placeholder={KICKOFF_HINT[kickoffKind] || KICKOFF_HINT.task}
                  />
                </label>
                <label className="agent-edit-full">
                  Repo
                  <input
                    type="text"
                    value={scopeRepo}
                    onChange={(e) => setScopeRepo(e.target.value)}
                    placeholder="org/repo  (the repo the steps run against)"
                  />
                </label>
              </div>
              <div className="agent-edit-footer">
                <button className="btn" onClick={() => setShowRun(false)} disabled={running}>
                  Cancel
                </button>
                <button className="btn btn-primary" onClick={doRun} disabled={running || !runTask.trim()}>
                  {running ? "Starting..." : <><Play size={12} /> Run flow</>}
                </button>
              </div>
            </div>
          </div>
        </ModalPortal>
      )}
    </div>
  );
}
