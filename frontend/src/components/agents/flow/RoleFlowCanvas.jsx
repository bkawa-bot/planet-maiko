import { useMemo } from "react";
import { ReactFlow, Background, Controls } from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import "./flow-theme.css";
import RoleNode from "./RoleNode";
import { roleMeta } from "../../../hooks/useAgentTypes";
import { kindColor, edgeValid } from "./kinds";

const nodeTypes = { role: RoleNode };

// Read-only Phase B map. Lay the roles out as character-card nodes with
// typed sockets, then draw a faint wire wherever one role's output kind
// matches another's input kind (e.g. Coder produces diff -> Reviewer
// accepts diff). No editing, no persistence, no run. This is where the
// warm canvas look gets nailed before the editor and engine land.
export default function RoleFlowCanvas({ types }) {
  const { nodes, edges } = useMemo(() => {
    const list = types || [];
    const COLS = 3;

    const nodes = list.map((t, i) => {
      const meta = roleMeta(t.id, list);
      return {
        id: t.id,
        type: "role",
        position: { x: (i % COLS) * 320, y: Math.floor(i / COLS) * 210 },
        data: { type: t, color: meta.color, Icon: meta.icon },
      };
    });

    const edges = [];
    for (const a of list) {
      for (const b of list) {
        if (a.id === b.id) continue;
        const aOut = a.output_kind || "diff";
        const bIn = b.input_kind || "task";
        if (edgeValid(aOut, bIn)) {
          edges.push({
            id: `${a.id}__${b.id}`,
            source: a.id,
            target: b.id,
            sourceHandle: "out",
            targetHandle: "in",
            className: "flow-edge",
            style: { stroke: kindColor(aOut) },
          });
        }
      }
    }

    return { nodes, edges };
  }, [types]);

  return (
    <div className="flow-canvas-wrap">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesConnectable={false}
        minZoom={0.3}
        maxZoom={1.5}
      >
        <Background gap={22} size={1.4} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {(types || []).length === 0 && (
        <div className="flow-empty">No roles to map yet.</div>
      )}
    </div>
  );
}
