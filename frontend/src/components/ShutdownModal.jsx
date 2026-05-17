import { useEffect, useState, useRef } from "react";
import { Power, Moon, Loader, Check, X } from "@icons";
import { api } from "../api/client";
import ModalPortal from "./ModalPortal";
import "./ShutdownModal.css";

// Each step has a running line ("putting agents to bed…") and a
// done-line template fed by the server's response counts. Order
// controls the ritual — light work first, server shutdown last.
const STEPS = [
  {
    name: "stop_agents",
    label: "Stop active agents",
    narrator: "Putting the agents to bed…",
    done: (r) => `${r.stopped || 0} agents tucked in`,
  },
  {
    name: "cleanup_worktrees",
    label: "Clean up worktrees",
    narrator: "Tidying up the workrooms…",
    done: (r) => {
      const parts = [];
      if (r.cleaned) parts.push(`${r.cleaned} cleaned`);
      if (r.orphaned) parts.push(`${r.orphaned} orphans swept`);
      return parts.join(" · ") || "nothing to clean";
    },
  },
  {
    name: "prune_pupdates",
    label: "Prune old pupdates",
    narrator: "Quieting old pupdates…",
    done: (r) => `shhhh'd ${r.deleted || 0} pupdates`,
  },
  {
    name: "prune_messages",
    label: "Prune old agent messages",
    narrator: "Archiving old conversations…",
    done: (r) => `${r.deleted || 0} messages archived`,
  },
  {
    name: "prune_signals",
    label: "Prune trained-on signals",
    narrator: "Clearing out yesterday's training notes…",
    done: (r) => `${r.deleted || 0} old signals swept`,
  },
  {
    name: "prune_dismissed",
    label: "Forget dismissed items",
    narrator: "Forgetting the stuff you let go of…",
    done: (r) => `${r.deleted || 0} dismissed items forgotten`,
  },
  {
    name: "stop_server",
    label: "Stop the server",
    narrator: "Dimming the lights…",
    done: () => "Goodnight 🌙",
    terminal: true,  // after this, no more API calls will succeed
  },
];

const PREVIEW_FIELDS = {
  stop_agents: "active_sessions",
  cleanup_worktrees: "worktrees",
  prune_pupdates: "pupdates",
  prune_messages: "agent_messages",
  prune_signals: "signals",
  prune_dismissed: "dismissed",
  stop_server: null,  // no count
};


export default function ShutdownModal({ onClose }) {
  const [stage, setStage] = useState("loading"); // loading | preview | running | done | error
  const [preview, setPreview] = useState(null);
  const [selected, setSelected] = useState(() => {
    // Default: everything checked. User can uncheck stop_server if they
    // just want cleanup without shutting the server down.
    const init = {};
    for (const s of STEPS) init[s.name] = true;
    return init;
  });
  const [log, setLog] = useState([]); // [{step, status, narrator, done?}]
  const [error, setError] = useState(null);
  const cancelled = useRef(false);

  useEffect(() => {
    // Reset the "cancelled" flag on every setup. In StrictMode dev,
    // React mounts, runs cleanup (which set cancelled=true), then
    // re-mounts. Without this reset, by the time the user clicked
    // "Tuck us in" the flag was already true and handleRun's first
    // iteration broke out immediately, leaving the progress stage
    // with an empty log and the server never actually stopped.
    cancelled.current = false;
    api.getShutdownPreview()
      .then((p) => { setPreview(p); setStage("preview"); })
      .catch((err) => { setError(err.message); setStage("error"); });
    return () => { cancelled.current = true; };
  }, []);

  const toggleStep = (name) => {
    setSelected((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const handleRun = async () => {
    const toRun = STEPS.filter((s) => selected[s.name]);
    if (toRun.length === 0) {
      onClose();
      return;
    }
    setStage("running");
    setLog([]);

    for (const step of toRun) {
      if (cancelled.current) break;
      // Push "running" entry
      setLog((l) => [...l, { step: step.name, status: "running", narrator: step.narrator }]);
      try {
        const result = await api.runShutdownStep(step.name);
        if (cancelled.current) break;
        // Terminal step (stop_server): show the goodnight line, stop here.
        // Any API call after this will fail with connection-refused.
        const doneLine = step.done(result || {});
        setLog((l) => {
          const updated = [...l];
          updated[updated.length - 1] = {
            step: step.name,
            status: "done",
            narrator: step.narrator,
            doneLine,
          };
          return updated;
        });
        if (step.terminal) {
          setStage("done");
          return;
        }
        // Small pause for narration rhythm. Enough to feel paced,
        // short enough not to slow the whole ritual.
        await new Promise((r) => setTimeout(r, 400));
      } catch (err) {
        if (step.terminal) {
          // Server likely died mid-response; that's the expected path.
          setLog((l) => {
            const updated = [...l];
            updated[updated.length - 1] = {
              step: step.name,
              status: "done",
              narrator: step.narrator,
              doneLine: "Goodnight 🌙",
            };
            return updated;
          });
          setStage("done");
          return;
        }
        setLog((l) => {
          const updated = [...l];
          updated[updated.length - 1] = {
            step: step.name,
            status: "error",
            narrator: step.narrator,
            doneLine: err.message || "something went wrong",
          };
          return updated;
        });
        // Don't bail — continue to the next step. Soft failure is fine
        // for cleanup; a stuck step shouldn't block a pupdate prune.
      }
    }
    if (!cancelled.current) setStage("done");
  };

  return (
    <ModalPortal>
    <div className="modal-overlay" onClick={stage === "running" ? undefined : onClose}>
      <div className={`shutdown-modal stage-${stage}`} onClick={(e) => e.stopPropagation()}>
        {stage === "loading" && (
          <div className="shutdown-loading">
            <Loader size={20} className="spin" />
            <p>Checking what needs tidying…</p>
          </div>
        )}

        {stage === "error" && (
          <div className="shutdown-error">
            <X size={20} />
            <p>{error}</p>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        )}

        {stage === "preview" && preview && (
          <PreviewStage
            preview={preview}
            selected={selected}
            onToggle={toggleStep}
            onCancel={onClose}
            onConfirm={handleRun}
          />
        )}

        {(stage === "running" || stage === "done") && (
          <ProgressStage
            log={log}
            done={stage === "done"}
            onClose={onClose}
          />
        )}
      </div>
    </div>
    </ModalPortal>
  );
}


function PreviewStage({ preview, selected, onToggle, onCancel, onConfirm }) {
  const totalPruned =
    (preview.pupdates || 0) +
    (preview.agent_messages || 0) +
    (preview.signals || 0) +
    (preview.dismissed || 0);

  return (
    <>
      <div className="shutdown-header">
        <Power size={18} />
        <h3>Settle in for the night?</h3>
      </div>
      <p className="shutdown-intro">
        Before Maiko sleeps, she can tidy up a few things. Everything below is
        reversible until you hit <strong>Tuck us in</strong>.
      </p>

      <div className="shutdown-steps-list">
        {STEPS.map((s) => {
          const countField = PREVIEW_FIELDS[s.name];
          const count = countField ? preview[countField] : null;
          const disabled = countField && count === 0 && s.name !== "stop_server";
          return (
            <label
              key={s.name}
              className={`shutdown-step-option ${disabled ? "dim" : ""}`}
            >
              <input
                type="checkbox"
                checked={selected[s.name] && !disabled}
                disabled={disabled}
                onChange={() => onToggle(s.name)}
              />
              <span className="shutdown-step-label">{s.label}</span>
              {count !== null && (
                <span className="shutdown-step-count">{count}</span>
              )}
            </label>
          );
        })}
      </div>

      <div className="shutdown-footer">
        <span className="shutdown-total">
          {totalPruned > 0 ? `About ${totalPruned} items will be pruned` : "Nothing to prune"}
        </span>
        <div className="shutdown-actions">
          <button className="btn" onClick={onCancel}>Not tonight</button>
          <button className="btn btn-primary" onClick={onConfirm}>
            <Moon size={12} /> Tuck us in
          </button>
        </div>
      </div>
    </>
  );
}


function ProgressStage({ log, done, onClose }) {
  return (
    <div className="shutdown-scene">
      <div className={`shutdown-scene-sprite ${done ? "asleep" : ""}`}>
        {done ? "🌙" : "🔥"}
      </div>
      <div className="shutdown-scene-caption">
        {done ? "Everyone's dreaming" : "Maiko is settling in for the night…"}
      </div>

      <div className="shutdown-log">
        {log.map((line, i) => (
          <div key={i} className={`shutdown-log-line status-${line.status}`}>
            {line.status === "running" && <Loader size={10} className="spin" />}
            {line.status === "done" && <Check size={10} />}
            {line.status === "error" && <X size={10} />}
            <span className="shutdown-log-narrator">{line.narrator}</span>
            {line.doneLine && (
              <span className="shutdown-log-done"> · {line.doneLine}</span>
            )}
          </div>
        ))}
      </div>

      {done && (
        <div className="shutdown-done-actions">
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      )}
    </div>
  );
}
