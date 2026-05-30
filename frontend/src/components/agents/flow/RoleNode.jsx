import { Handle, Position } from "@xyflow/react";
import { kindColor } from "./kinds";

// A role rendered as a warm character card on the flow canvas. The
// sockets are colored doorways: the input on the left edge, the output
// on the right. Read-only for Phase B (isConnectable=false); the same
// component backs the interactive editor later.
export default function RoleNode({ data, isConnectable }) {
  const { type, color, Icon, status } = data;
  const accepts = (type.accepts && type.accepts.length) ? type.accepts : [type.input_kind || "task"];
  const primaryIn = accepts[0] || "task";
  const outKind = type.output_kind || "diff";

  return (
    <div className={`flow-role-node${status ? ` status-${status}` : ""}`} style={{ borderColor: color }}>
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
        <span className="flow-role-icon" style={{ color }}>
          {Icon ? <Icon size={20} /> : null}
        </span>
        <div className="flow-role-title">
          <div className="flow-role-name">{type.name}</div>
          <div className="flow-role-sockets">
            <span style={{ color: kindColor(primaryIn) }}>{accepts.join("/")}</span>
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
        isConnectable={isConnectable}
      />
    </div>
  );
}
