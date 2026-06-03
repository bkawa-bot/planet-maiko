import { useState } from "react";
import { BaseEdge, EdgeLabelRenderer, useReactFlow } from "@xyflow/react";

// A loop (back-)edge in the flow editor. Custom because:
//   1. It draws an EXPLICIT arc — a quadratic bezier bulged perpendicular to
//      the straight line by a fixed margin — so a back-edge always reads as a
//      visible loop, regardless of node layout, instead of collapsing onto
//      the forward wire (native curvature wasn't enough on real layouts).
//   2. It carries an editable round-count badge (↻ N). The input uses a local
//      draft + commits on blur/Enter, and stops keydown propagation so React
//      Flow's delete-key handling doesn't eat Backspace (which was deleting
//      the whole edge mid-type). N writes back to data.maxLoops, the cap the
//      executor reads.
export default function LoopEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  markerEnd,
}) {
  const { setEdges } = useReactFlow();
  const [draft, setDraft] = useState(String((data && data.maxLoops) || 3));

  // Explicit arc: bulge perpendicular to the source->target line by a margin
  // that scales gently with distance, so it's always a clear loop.
  const mx = (sourceX + targetX) / 2;
  const my = (sourceY + targetY) / 2;
  const dx = targetX - sourceX;
  const dy = targetY - sourceY;
  const len = Math.hypot(dx, dy) || 1;
  const bulge = Math.min(130, Math.max(55, len * 0.3));
  const px = (-dy / len) * bulge;
  const py = (dx / len) * bulge;
  const edgePath = `M ${sourceX},${sourceY} Q ${mx + px},${my + py} ${targetX},${targetY}`;
  // The quadratic's apex sits ~halfway to the control point; park the badge there.
  const labelX = mx + px * 0.5;
  const labelY = my + py * 0.5;

  const commit = () => {
    const n = Math.max(1, Math.min(20, parseInt(draft, 10) || 3));
    setDraft(String(n));
    setEdges((eds) =>
      eds.map((e) =>
        e.id === id ? { ...e, data: { ...(e.data || {}), maxLoops: n } } : e
      )
    );
  };

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{ stroke: "#d9a93a", strokeWidth: 2, strokeDasharray: "6 4" }}
      />
      <EdgeLabelRenderer>
        <div
          className="flow-loop-badge nodrag nopan"
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          }}
          title="Loop: this step sends the target back, up to N rounds"
        >
          <span className="flow-loop-icon">↻</span>
          <input
            type="number"
            min={1}
            max={20}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              e.stopPropagation(); // keep Backspace/Delete from deleting the edge
              if (e.key === "Enter") {
                e.preventDefault();
                e.currentTarget.blur();
              }
            }}
            onClick={(e) => e.stopPropagation()}
            className="flow-loop-input nodrag nopan"
            title="Max loop rounds before the flow moves on"
          />
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
