import { useState } from "react";
import { Handle, Position, useReactFlow } from "@xyflow/react";
import { X } from "@icons";
import { useFlowOptions } from "./useFlowOptions";

const SUBTYPES = [
  { value: "create_memo", label: "Create a memo" },
  { value: "create_task", label: "Create a task" },
  { value: "run_agent_job", label: "Run a skill / agent job" },
];

// A non-agent action node: a side-effect run inline when the flow reaches it.
//   create_memo / create_task — seeded with its input (the pupdate that fired
//     a trigger, or an upstream agent's output).
//   run_agent_job — fire a skill / one-shot agent job (fire-and-forget; the
//     pack picks it up next tick). This is how a schedule trigger "runs a
//     skill": [schedule] -> [run a skill]. Config writes to node.data.config,
//     read by the executor.
export default function ActionNode({ id, data }) {
  const { setNodes, deleteElements } = useReactFlow();
  const { agentJobKinds } = useFlowOptions();
  const cfg = data.config || {};
  const subtype = cfg.subtype || "create_memo";
  const [title, setTitle] = useState(cfg.title || "");
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

  // Switching to run_agent_job seeds a default kind so saving without touching
  // the picker still persists one (keeps it in sync with the shown default).
  const onSubtype = (v) =>
    v === "run_agent_job"
      ? patch({ subtype: v, job_kind: cfg.job_kind || "cartograph" })
      : patch({ subtype: v });

  return (
    <div
      className="flow-action-node"
      title="Runs a side-effect when the flow reaches it"
    >
      {data.editable && (
        <button
          type="button"
          className="flow-node-delete"
          title="Remove action"
          onClick={(e) => { stop(e); deleteElements({ nodes: [{ id }] }); }}
        >
          <X size={11} />
        </button>
      )}
      <Handle type="target" position={Position.Left} id="in" className="flow-socket" />
      <div className="flow-action-head">Then</div>
      <select
        className="flow-action-select nodrag nopan"
        value={subtype}
        onChange={(e) => onSubtype(e.target.value)}
        onClick={stop}
      >
        {SUBTYPES.map((s) => (
          <option key={s.value} value={s.value}>{s.label}</option>
        ))}
      </select>

      {subtype === "run_agent_job" ? (
        <>
          <select
            className="flow-action-select nodrag nopan"
            value={cfg.job_kind || "cartograph"}
            onChange={(e) => patch({ job_kind: e.target.value })}
            onClick={stop}
            title="Which skill / job kind to run"
          >
            {agentJobKinds.map((k) => (
              <option key={k.value} value={k.value}>{k.label}</option>
            ))}
          </select>
          <textarea
            className="flow-action-input nodrag nopan"
            rows={2}
            value={input}
            placeholder="input for the skill (what to focus on)"
            onChange={(e) => setInput(e.target.value)}
            onBlur={() => patch({ input: input.trim() })}
            onKeyDown={stop}
            onClick={stop}
          />
          <input
            className="flow-action-input nodrag nopan"
            value={repo}
            placeholder="repo (optional, org/name)"
            onChange={(e) => setRepo(e.target.value)}
            onBlur={() => patch({ repo: repo.trim() })}
            onKeyDown={(e) => { stop(e); if (e.key === "Enter") e.currentTarget.blur(); }}
            onClick={stop}
          />
        </>
      ) : (
        <input
          className="flow-action-input nodrag nopan"
          value={title}
          placeholder="title (optional)"
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => patch({ title: title.trim() })}
          onKeyDown={(e) => { stop(e); if (e.key === "Enter") e.currentTarget.blur(); }}
          onClick={stop}
        />
      )}

      <Handle type="source" position={Position.Right} id="out" className="flow-socket" />
    </div>
  );
}
