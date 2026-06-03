import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useReactFlow,
} from "@xyflow/react";

// A loop (back-)edge in the flow editor. Two reasons it's a custom edge:
//   1. Extra curvature so it bows clearly away from the forward wire between
//      the same two nodes, instead of collapsing on top of it.
//   2. An editable round-count badge (↻ N). N writes straight back to the
//      edge's data.maxLoops via useReactFlow().setEdges, which the executor
//      reads as the loop cap. Using the hook (not a callback threaded through
//      edge.data) keeps data JSON-clean and works for both freshly-drawn and
//      reloaded edges.
export default function LoopEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
}) {
  const { setEdges } = useReactFlow();
  const maxLoops = (data && data.maxLoops) || 3;

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.6,
  });

  const setMax = (raw) => {
    const n = Math.max(1, Math.min(20, parseInt(raw, 10) || maxLoops));
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
          title="Loop: this step sends the target back up to N rounds"
        >
          <span className="flow-loop-icon">↻</span>
          <input
            type="number"
            min={1}
            max={20}
            value={maxLoops}
            onChange={(e) => setMax(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            className="flow-loop-input"
            title="Max loop rounds before the flow moves on"
          />
        </div>
      </EdgeLabelRenderer>
    </>
  );
}
