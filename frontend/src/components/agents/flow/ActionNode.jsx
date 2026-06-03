import { useState } from "react";
import { Handle, Position, useReactFlow } from "@xyflow/react";
import { X } from "@icons";

const SUBTYPES = [
  { value: "create_memo", label: "Create memo" },
  { value: "create_task", label: "Create task" },
];

// A non-agent action node: a side-effect run inline when the flow reaches it
// (drop a memo / create a task), seeded with its input — the pupdate that
// fired a trigger, or an upstream agent's output. Config writes to
// node.data.config {subtype, title}, which the executor reads.
export default function ActionNode({ id, data }) {
  const { setNodes, deleteElements } = useReactFlow();
  const cfg = data.config || {};
  const [title, setTitle] = useState(cfg.title || "");

  const patch = (next) => {
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, config: { ...(n.data.config || {}), ...next } } }
          : n
      )
    );
  };

  return (
    <div
      className="flow-action-node"
      title="Runs a side-effect (memo / task) when the flow reaches it"
    >
      {data.editable && (
        <button
          type="button"
          className="flow-node-delete"
          title="Remove action"
          onClick={(e) => { e.stopPropagation(); deleteElements({ nodes: [{ id }] }); }}
        >
          <X size={11} />
        </button>
      )}
      <Handle type="target" position={Position.Left} id="in" className="flow-socket" />
      <div className="flow-action-head">Then</div>
      <select
        className="flow-action-select nodrag nopan"
        value={cfg.subtype || "create_memo"}
        onChange={(e) => patch({ subtype: e.target.value })}
        onClick={(e) => e.stopPropagation()}
      >
        {SUBTYPES.map((s) => (
          <option key={s.value} value={s.value}>{s.label}</option>
        ))}
      </select>
      <input
        className="flow-action-input nodrag nopan"
        value={title}
        placeholder="title (optional)"
        onChange={(e) => setTitle(e.target.value)}
        onBlur={() => patch({ title: title.trim() })}
        onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") e.currentTarget.blur(); }}
        onClick={(e) => e.stopPropagation()}
      />
      <Handle type="source" position={Position.Right} id="out" className="flow-socket" />
    </div>
  );
}
