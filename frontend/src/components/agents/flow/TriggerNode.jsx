import { useState } from "react";
import { Handle, Position, useReactFlow } from "@xyflow/react";
import { Crystal, X } from "@icons";

// A pupdate trigger node: the entry point of an event-driven flow. "When a
// pupdate of type <X> arrives, start this flow, seeded with that pupdate."
// The type edits inline and writes to node.data.config.pupdate_type, which
// the trigger-eval engine reads. Output-only — no input socket, so nothing
// can wire INTO it.
export default function TriggerNode({ id, data }) {
  const { setNodes, deleteElements } = useReactFlow();
  const cfg = data.config || {};
  const [draft, setDraft] = useState(cfg.pupdate_type || "");

  const commit = () => {
    const v = draft.trim();
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, config: { ...(n.data.config || {}), pupdate_type: v } } }
          : n
      )
    );
  };

  return (
    <div
      className="flow-trigger-node"
      title="Fires this flow when a matching pupdate arrives"
    >
      {data.editable && (
        <button
          type="button"
          className="flow-node-delete"
          title="Remove trigger"
          onClick={(e) => { e.stopPropagation(); deleteElements({ nodes: [{ id }] }); }}
        >
          <X size={11} />
        </button>
      )}
      <div className="flow-trigger-head">
        <Crystal size={14} className="flow-trigger-icon" />
        <span className="flow-trigger-when">When a pupdate of type</span>
      </div>
      <input
        className="flow-trigger-input nodrag nopan"
        value={draft}
        placeholder="e.g. incident"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") e.currentTarget.blur(); }}
        onClick={(e) => e.stopPropagation()}
        title="Pupdate type to fire on (blank = any)"
      />
      <div className="flow-trigger-foot">arrives, start this flow</div>
      <Handle
        type="source"
        position={Position.Right}
        id="out"
        className="flow-socket"
        style={{ background: "#d9a93a" }}
      />
    </div>
  );
}
