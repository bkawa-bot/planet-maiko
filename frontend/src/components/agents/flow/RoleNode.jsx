import { Handle, Position } from "@xyflow/react";
import { kindColor } from "./kinds";

// A role rendered as a warm character card on the flow canvas. The
// sockets are colored doorways: the input on the left edge, the output
// on the right. Read-only for Phase B (isConnectable=false); the same
// component backs the interactive editor later.
export default function RoleNode({ data }) {
  const { type, color, Icon } = data;
  const inKind = type.input_kind || "task";
  const outKind = type.output_kind || "diff";

  return (
    <div className="flow-role-node" style={{ borderColor: color }}>
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        className="flow-socket"
        style={{ background: kindColor(inKind) }}
        title={`accepts ${inKind}`}
        isConnectable={false}
      />

      <div className="flow-role-head">
        <span className="flow-role-icon" style={{ color }}>
          {Icon ? <Icon size={20} /> : null}
        </span>
        <div className="flow-role-title">
          <div className="flow-role-name">{type.name}</div>
          <div className="flow-role-sockets">
            <span style={{ color: kindColor(inKind) }}>{inKind}</span>
            <span className="flow-sock-arrow">→</span>
            <span style={{ color: kindColor(outKind) }}>{outKind}</span>
          </div>
        </div>
      </div>

      {type.description && <div className="flow-role-desc">{type.description}</div>}

      <Handle
        type="source"
        position={Position.Right}
        id="out"
        className="flow-socket"
        style={{ background: kindColor(outKind) }}
        title={`produces ${outKind}`}
        isConnectable={false}
      />
    </div>
  );
}
