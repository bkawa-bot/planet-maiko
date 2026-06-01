import { Handle, Position, useReactFlow } from "@xyflow/react";
import { X } from "@icons";
import { kindColor } from "./kinds";
import CardAvatar from "../../CardAvatar";

// A role rendered as a warm character card on the flow canvas. The
// sockets are colored doorways: the input on the left edge, the output
// on the right. Read-only for Phase B (isConnectable=false); the same
// component backs the interactive editor later.
export default function RoleNode({ id, data, isConnectable }) {
  const { type, color, Icon, status, editable, sub, avatar } = data;
  const rf = useReactFlow();
  const accepts = (type.accepts && type.accepts.length) ? type.accepts : [type.input_kind || "task"];
  const primaryIn = accepts[0] || "task";
  const outKind = type.output_kind || "diff";
  // Behavior hints, surfaced as pills so the implicit runtime behavior is
  // legible at build time: a "tasks" producer fans the next step out into
  // one instance per task; a "diff" consumer (a reviewer) can loop changes
  // back to its coder.
  const fansOut = outKind === "tasks";
  const loops = accepts.includes("diff");

  return (
    <div
      className={`flow-role-node${status ? ` status-${status}` : ""}`}
      style={{ borderColor: color }}
      title={type.description || type.name}
    >
      {editable && (
        <button
          type="button"
          className="flow-node-delete"
          title="Remove step"
          onClick={(e) => { e.stopPropagation(); rf.deleteElements({ nodes: [{ id }] }); }}
        >
          <X size={11} />
        </button>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        className="flow-socket"
        style={{ background: kindColor(primaryIn) }}
        title={`accepts ${accepts.join(", ")}`}
        isConnectable={isConnectable}
      />

      <div className="flow-role-head">
        {avatar ? (
          <CardAvatar cardId={avatar} size={34} className="flow-role-avatar" />
        ) : (
          <span className="flow-role-icon" style={{ color }}>
            {Icon ? <Icon size={20} /> : null}
          </span>
        )}
        <div className="flow-role-title">
          <div className="flow-role-name">{type.name}</div>
          <div className="flow-role-sockets">
            <span style={{ color: kindColor(primaryIn) }}>{primaryIn}</span>
            <span className="flow-sock-arrow">→</span>
            <span style={{ color: kindColor(outKind) }}>{outKind}</span>
          </div>
          {(fansOut || loops) && (
            <div className="flow-role-flags">
              {fansOut && (
                <span
                  className="flow-role-flag flag-fan"
                  title="Produces a task list, so the next step runs once per task (fans out into N at run time)"
                >
                  fans out
                </span>
              )}
              {loops && (
                <span
                  className="flow-role-flag flag-loop"
                  title="Reviews a diff and can send it back to the coder to revise, up to 3 rounds"
                >
                  loops ≤3
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {sub && <div className="flow-role-sub" title={sub}>{sub}</div>}

      <Handle
        type="source"
        position={Position.Right}
        id="out"
        className="flow-socket"
        style={{ background: kindColor(outKind) }}
        title={`produces ${outKind}`}
        isConnectable={isConnectable}
      />
    </div>
  );
}
