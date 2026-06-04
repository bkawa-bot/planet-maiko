import { useMemo, useState } from "react";
import { Handle, Position, useReactFlow } from "@xyflow/react";
import { Crystal, Clock, X } from "@icons";
import { useFlowOptions } from "./useFlowOptions";

const UNITS = ["minutes", "hours", "days"];
const PRIORITIES = ["low", "normal", "high", "urgent"];
const DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

// A trigger node = the entry of an event-driven flow. Its kind is fixed at
// creation by which palette button dropped it (config.trigger_kind), so there
// is no mode switch on the node itself:
//   "pupdate"  — fire when a matching pupdate arrives. Pick the type from the
//                live registry (grouped GitHub / Linear / Agents / ...);
//                optionally narrow by priority / source.
//   "schedule" — fire every N minutes/hours/days. The WORK lives downstream:
//                wire this into a "run a skill" action or a role node. An
//                optional seed repo scopes that first step.
// Output-only — nothing wires in. Config writes to node.data.config, read by
// the trigger-eval engine.
export default function TriggerNode({ id, data }) {
  const { setNodes, deleteElements } = useReactFlow();
  const { pupdateTypes, sources } = useFlowOptions();
  const cfg = data.config || {};
  const isSchedule = cfg.trigger_kind === "schedule";

  // Free-text / number fields keep a local draft and commit on blur so typing
  // doesn't churn the whole canvas on every keystroke; selects patch directly.
  const [ival, setIval] = useState(String(cfg.interval_value || 1));
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

  // Toggle a weekday in/out of the clock schedule's `days` (empty = daily).
  const toggleDay = (i) => {
    const cur = cfg.days || [];
    patch({
      days: cur.includes(i)
        ? cur.filter((d) => d !== i)
        : [...cur, i].sort((a, b) => a - b),
    });
  };

  // pupdate types grouped into <optgroup>s by their `group` (GitHub, Linear,
  // Agents, ...), preserving the backend's order within each group.
  const grouped = useMemo(() => {
    const out = [];
    const idx = {};
    for (const t of pupdateTypes) {
      const g = t.group || "Other";
      if (!(g in idx)) { idx[g] = out.length; out.push([g, []]); }
      out[idx[g]][1].push(t);
    }
    return out;
  }, [pupdateTypes]);

  return (
    <div
      className="flow-trigger-node"
      title={isSchedule
        ? "Fires this flow on a schedule"
        : "Fires this flow when a matching pupdate arrives"}
    >
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

      {isSchedule ? (
        <>
          <div className="flow-trigger-head">
            <Clock size={13} className="flow-trigger-icon" />
            <span>On a schedule</span>
          </div>
          <select
            className="flow-trigger-select nodrag nopan"
            value={cfg.schedule_kind || "interval"}
            onChange={(e) => patch({ schedule_kind: e.target.value })}
            onClick={stop}
          >
            <option value="interval">every N minutes / hours / days</option>
            <option value="clock">at a set time</option>
          </select>
          {(cfg.schedule_kind || "interval") === "clock" ? (
            <>
              <div className="flow-trigger-row">
                <span>at</span>
                <input
                  type="time"
                  className="flow-trigger-time nodrag nopan"
                  value={cfg.at || "09:00"}
                  onChange={(e) => patch({ at: e.target.value })}
                  onKeyDown={stop}
                  onClick={stop}
                />
              </div>
              <div className="flow-trigger-days">
                {DAYS.map((d, i) => (
                  <button
                    key={i}
                    type="button"
                    className={"flow-day" + ((cfg.days || []).includes(i) ? " on" : "")}
                    onClick={(e) => { stop(e); toggleDay(i); }}
                    title={DAY_NAMES[i]}
                  >
                    {d}
                  </button>
                ))}
              </div>
              <div className="flow-trigger-foot">
                {(cfg.days || []).length ? "on the picked days" : "every day"}
              </div>
            </>
          ) : (
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
          )}
          <input
            className="flow-trigger-input nodrag nopan"
            value={repo}
            placeholder="repo (optional, org/name)"
            onChange={(e) => setRepo(e.target.value)}
            onBlur={() => patch({ repo: repo.trim() })}
            onKeyDown={(e) => { stop(e); if (e.key === "Enter") e.currentTarget.blur(); }}
            onClick={stop}
          />
          <div className="flow-trigger-foot">
            Wire into a “run a skill” action or a role to do the work.
          </div>
        </>
      ) : (
        <>
          <div className="flow-trigger-head">
            <Crystal size={13} className="flow-trigger-icon" />
            <span>When a pupdate arrives</span>
          </div>
          <select
            className="flow-trigger-select nodrag nopan"
            value={cfg.pupdate_type || ""}
            onChange={(e) => patch({ pupdate_type: e.target.value })}
            onClick={stop}
            title="Pupdate type to fire on"
          >
            <option value="">any type</option>
            {grouped.map(([g, items]) => (
              <optgroup key={g} label={g}>
                {items.map((t) => (
                  <option key={t.name} value={t.name}>{t.label || t.name}</option>
                ))}
              </optgroup>
            ))}
          </select>
          <div className="flow-trigger-row">
            <select
              className="flow-trigger-select nodrag nopan"
              value={cfg.priority || ""}
              onChange={(e) => patch({ priority: e.target.value })}
              onClick={stop}
              title="Only fire at this priority"
            >
              <option value="">any priority</option>
              {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <select
              className="flow-trigger-select nodrag nopan"
              value={cfg.source || ""}
              onChange={(e) => patch({ source: e.target.value })}
              onClick={stop}
              title="Only fire from this source"
            >
              <option value="">any source</option>
              {sources.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
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
