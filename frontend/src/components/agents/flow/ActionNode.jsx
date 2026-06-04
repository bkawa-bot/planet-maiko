import { useState } from "react";
import { Handle, Position, useReactFlow } from "@xyflow/react";
import { X } from "@icons";
import { useFlowOptions } from "./useFlowOptions";

const SUBTYPES = [
  { value: "create_memo", label: "Create a memo" },
  { value: "create_task", label: "Create a task" },
  { value: "run_agent_job", label: "Run a skill / agent job" },
  { value: "complete_linked_task", label: "Close linked task" },
];
const PRIORITIES = ["low", "normal", "high", "urgent"];

// A non-agent action node, run when the flow reaches it.
//   create_memo — drop a memo (title / priority / url config; body = its
//     input; defaults to the triggering pupdate's title/body/url).
//   create_task — drop a task on the board.
//   run_agent_job — fire a skill / one-shot job. This one is an AWAITED step
//     (output flows downstream), spawned in the run loop, not inline.
//   complete_linked_task — close the task(s) linked to the triggering
//     pupdate's PR (matches the old "Close linked task" automation).
// Config writes to node.data.config, read by the executor.
export default function ActionNode({ id, data }) {
  const { setNodes, deleteElements } = useReactFlow();
  const { agentJobKinds } = useFlowOptions();
  const cfg = data.config || {};
  const subtype = cfg.subtype || "create_memo";
  const [title, setTitle] = useState(cfg.title || "");
  const [input, setInput] = useState(cfg.input || "");
  const [repo, setRepo] = useState(cfg.repo || "");
  const [url, setUrl] = useState(cfg.url || "");

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

  const titleField = (
    <input
      className="flow-action-input nodrag nopan"
      value={title}
      placeholder="title (optional)"
      onChange={(e) => setTitle(e.target.value)}
      onBlur={() => patch({ title: title.trim() })}
      onKeyDown={(e) => { stop(e); if (e.key === "Enter") e.currentTarget.blur(); }}
      onClick={stop}
    />
  );

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
      ) : subtype === "create_memo" ? (
        <>
          {titleField}
          <select
            className="flow-action-select nodrag nopan"
            value={cfg.priority || "normal"}
            onChange={(e) => patch({ priority: e.target.value })}
            onClick={stop}
            title="Memo priority"
          >
            {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <input
            className="flow-action-input nodrag nopan"
            value={url}
            placeholder="click-through url (optional)"
            onChange={(e) => setUrl(e.target.value)}
            onBlur={() => patch({ url: url.trim() })}
            onKeyDown={(e) => { stop(e); if (e.key === "Enter") e.currentTarget.blur(); }}
            onClick={stop}
          />
        </>
      ) : subtype === "complete_linked_task" ? (
        <div className="flow-action-foot">
          Closes the task(s) linked to the triggering pupdate’s PR.
        </div>
      ) : (
        titleField
      )}

      <Handle type="source" position={Position.Right} id="out" className="flow-socket" />
    </div>
  );
}
