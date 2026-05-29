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
import { X, Save } from "@icons";
import RoleNode from "./RoleNode";
import { roleMeta } from "../../../hooks/useAgentTypes";
import { kindColor } from "./kinds";
import { api } from "../../../api/client";
import { showToast } from "../../Toast";

const nodeTypes = { role: RoleNode };

// Hydrate the saved graph blob into React Flow nodes/edges. A node
// references a role by agent_type; we re-resolve its live color + icon
// from roleMeta so a role re-skin shows up without re-saving the flow.
function buildInitial(workflow, types) {
  const g = (workflow && workflow.graph) || { nodes: [], edges: [] };
  const list = types || [];
  const byId = Object.fromEntries(list.map((t) => [t.id, t]));

  const nodes = (g.nodes || []).map((n) => {
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
      data: { type: t, color: meta.color, Icon: meta.icon },
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
export default function FlowEditor({ workflow, types, onSaved, onClose }) {
  const initial = useMemo(() => buildInitial(workflow, types), [workflow, types]);
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges);
  const [name, setName] = useState((workflow && workflow.name) || "Untitled flow");
  const [saving, setSaving] = useState(false);
  const [rf, setRf] = useState(null);

  // A wire is valid only when the producer's output kind equals the
  // consumer's input kind. React Flow calls this during a drag and
  // refuses the drop on false.
  const isValidConnection = useCallback(
    (conn) => {
      const src = nodes.find((n) => n.id === conn.source);
      const tgt = nodes.find((n) => n.id === conn.target);
      if (!src || !tgt) return false;
      const out = src.data.type.output_kind || "diff";
      const inn = tgt.data.type.input_kind || "task";
      return out === inn;
    },
    [nodes]
  );

  const onConnect = useCallback(
    (conn) => {
      const src = nodes.find((n) => n.id === conn.source);
      const kind = (src && src.data.type.output_kind) || "diff";
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
        data: { type: role, color: meta.color, Icon: meta.icon },
      })
    );
  };

  const save = async () => {
    setSaving(true);
    try {
      const graph = {
        nodes: nodes.map((n) => ({
          id: n.id,
          agent_type: n.data.type.id,
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
      };
      const body = { name: name.trim() || "Untitled flow", graph };
      const saved = workflow && workflow.id
        ? await api.updateWorkflow(workflow.id, body)
        : await api.createWorkflow(body);
      onSaved?.(saved);
    } catch (err) {
      showToast(err.message || "Save failed", "high");
      setSaving(false);
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
        <button className="btn btn-sm" onClick={onClose} disabled={saving}>
          <X size={12} /> Close
        </button>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
          {saving ? "Saving..." : <><Save size={12} /> Save</>}
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
    </div>
  );
}
