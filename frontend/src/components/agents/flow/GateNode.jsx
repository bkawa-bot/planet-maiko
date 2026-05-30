import { Handle, Position, useReactFlow } from "@xyflow/react";
import { X, Check, CombinationLock } from "@icons";

// An approval gate: a control node, not an agent. In the editor it's a
// placeable pass-through (any wire to/from it is valid). In the run view
// it parks the flow and shows Approve / Reject when it's awaiting you.
export default function GateNode({ id, data, isConnectable }) {
  const { status, editable, onApprove, onReject } = data;
  const rf = useReactFlow();
  const awaiting = status === "awaiting_approval";

  return (
    <div className={`flow-gate-node${status ? ` status-${status}` : ""}${awaiting ? " awaiting" : ""}`}>
      {editable && (
        <button
          type="button"
          className="flow-node-delete"
          title="Remove gate"
          onClick={(e) => { e.stopPropagation(); rf.deleteElements({ nodes: [{ id }] }); }}
        >
          <X size={11} />
        </button>
      )}

      <Handle
        type="target"
        position={Position.Left}
        id="in"
        className="flow-socket flow-socket-gate"
        isConnectable={isConnectable}
      />

      <div className="flow-gate-head">
        <CombinationLock size={15} />
        <span className="flow-gate-label">Approval</span>
      </div>

      {awaiting && onApprove && (
        <div className="flow-gate-actions">
          <button
            type="button"
            className="flow-gate-approve"
            onClick={(e) => { e.stopPropagation(); onApprove(); }}
          >
            <Check size={11} /> Approve
          </button>
          <button
            type="button"
            className="flow-gate-reject"
            onClick={(e) => { e.stopPropagation(); onReject(); }}
          >
            Reject
          </button>
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        id="out"
        className="flow-socket flow-socket-gate"
        isConnectable={isConnectable}
      />
    </div>
  );
}
