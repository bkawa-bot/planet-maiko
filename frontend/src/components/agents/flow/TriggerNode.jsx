import { useState } from "react";
import { Handle, Position, useReactFlow } from "@xyflow/react";
import { Crystal, X } from "@icons";

const UNITS = ["minutes", "hours", "days"];

// A trigger node: the entry of an event-driven flow. Two modes via
// config.trigger_kind — "pupdate" (fire when a matching pupdate arrives) or
// "schedule" (fire every N minutes/hours/days, seeding the flow with the
// node's input). Output-only — nothing wires into it. Config writes to
// node.data.config, read by the trigger-eval engine.
export default function TriggerNode({ id, data }) {
  const { setNodes, deleteElements } = useReactFlow();
  const cfg = data.config || {};
  const mode = cfg.trigger_kind || "pupdate";
  const [ptype, setPtype] = useState(cfg.pupdate_type || "");
  const [ival, setIval] = useState(String(cfg.interval_value || 1));
  const [input, setInput] = useState(cfg.input || "");
  const [repo, setRepo] = useState(cfg.repo || "");

  const patch = (next) => {
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, config: { ...(n.data.config || {}), ...next } } }
          : n
      )
    );
  };
  const stop = (e) => e.stopPropagation();

  return (
    <div className="flow-trigger-node" title="Fires this flow on the chosen event">
      {data.editable && (
        <button
          type="button"
          className="flow-node-delete"
          title="Remove trigger"
          onClick={(e) => { stop(e); deleteElements({ nodes: [{ id }] }); }}
        >
          <X size={11} />
        </button>
      )}
      <div className="flow-trigger-head">
        <Crystal size={14} className="flow-trigger-icon" />
        <span>When</span>
      </div>
      <select
        className="flow-trigger-select nodrag nopan"
        value={mode}
        onChange={(e) => patch({ trigger_kind: e.target.value })}
        onClick={stop}
      >
        <option value="pupdate">a pupdate arrives</option>
        <option value="schedule">on a schedule</option>
      </select>

      {mode === "pupdate" ? (
        <input
          className="flow-trigger-input nodrag nopan"
          value={ptype}
          placeholder="type (blank = any)"
          onChange={(e) => setPtype(e.target.value)}
          onBlur={() => patch({ pupdate_type: ptype.trim() })}
          onKeyDown={(e) => { stop(e); if (e.key === "Enter") e.currentTarget.blur(); }}
          onClick={stop}
          title="Pupdate type to fire on (blank = any)"
        />
      ) : (
        <>
          <div className="flow-trigger-row">
            <span>every</span>
            <input
              type="number"
              min={1}
              className="flow-trigger-num nodrag nopan"
              value={ival}
              onChange={(e) => setIval(e.target.value)}
              onBlur={() => patch({ interval_value: Math.max(1, parseInt(ival, 10) || 1) })}
              onKeyDown={(e) => { stop(e); if (e.key === "Enter") e.currentTarget.blur(); }}
              onClick={stop}
            />
            <select
              className="flow-trigger-select nodrag nopan"
              value={cfg.interval_unit || "hours"}
              onChange={(e) => patch({ interval_unit: e.target.value })}
              onClick={stop}
            >
              {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
          </div>
          <textarea
            className="flow-trigger-input nodrag nopan"
            rows={2}
            value={input}
            placeholder="what to do (seeded to the next step)"
            onChange={(e) => setInput(e.target.value)}
            onBlur={() => patch({ input: input.trim() })}
            onKeyDown={stop}
            onClick={stop}
          />
          <input
            className="flow-trigger-input nodrag nopan"
            value={repo}
            placeholder="repo (optional, org/name)"
            onChange={(e) => setRepo(e.target.value)}
            onBlur={() => patch({ repo: repo.trim() })}
            onKeyDown={(e) => { stop(e); if (e.key === "Enter") e.currentTarget.blur(); }}
            onClick={stop}
          />
        </>
      )}
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
