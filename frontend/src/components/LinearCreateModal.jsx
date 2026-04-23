import { useState, useEffect } from "react";
import { X, Loader, Send } from "lucide-react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import "./LinearCreateModal.css";

/**
 * Send-to-Linear modal. Opens from a task's "Send to Linear" button
 * (TaskCard), fetches the team's workflow states / labels / cycles /
 * projects / members on mount, and lets the user fill in the Linear-
 * specific bits before firing issueCreate.
 *
 * Defaults match Linear's ergonomic expectations:
 *   - state → first "unstarted" (Todo / Backlog)
 *   - cycle → activeCycle if the team has cycles enabled
 *   - assignee → the viewer (API-key owner)
 *   - priority → mapped from task.priority name
 *   - dueDate → task.due_date if set
 *
 * sync_close is opt-in and default off — the checkbox is labeled so
 * the user knows nothing happens to Linear unless they flip it.
 */
export default function LinearCreateModal({ task, onClose, onCreated }) {
  const [meta, setMeta] = useState(null);
  const [loadingMeta, setLoadingMeta] = useState(true);
  const [metaError, setMetaError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const [title, setTitle] = useState(task.title || "");
  const [description, setDescription] = useState(
    task.extra?.description || task.metadata?.description || "",
  );
  const [stateId, setStateId] = useState("");
  const [priority, setPriority] = useState("");
  const [cycleId, setCycleId] = useState("");
  const [labelIds, setLabelIds] = useState([]);
  const [assigneeId, setAssigneeId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [estimate, setEstimate] = useState("");
  const [dueDate, setDueDate] = useState(task.due_date || "");
  const [syncClose, setSyncClose] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getLinearTeamMeta()
      .then((m) => {
        if (cancelled) return;
        setMeta(m);
        const firstUnstarted = (m.states || []).find((s) => s.type === "unstarted");
        const firstBacklog = (m.states || []).find((s) => s.type === "backlog");
        setStateId(firstUnstarted?.id || firstBacklog?.id || m.states?.[0]?.id || "");
        setCycleId(m.activeCycle?.id || "");
        setAssigneeId(m.defaultAssigneeId || "");
        // Seed priority from task.priority name
        const pMap = { urgent: "1", high: "2", normal: "3", low: "4" };
        setPriority(pMap[(task.priority || "normal").toLowerCase()] || "3");
      })
      .catch((err) => {
        if (cancelled) return;
        setMetaError(err?.message || "Couldn't load Linear team");
      })
      .finally(() => {
        if (!cancelled) setLoadingMeta(false);
      });
    return () => { cancelled = true; };
  }, []);

  const toggleLabel = (id) => {
    setLabelIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (submitting || loadingMeta) return;
    if (!title.trim()) {
      showToast("Title required", "high");
      return;
    }
    setSubmitting(true);
    try {
      const payload = {
        title: title.trim(),
        description: description.trim() || undefined,
        state_id: stateId || undefined,
        priority: priority === "" ? undefined : parseInt(priority, 10),
        cycle_id: cycleId || undefined,
        label_ids: labelIds.length ? labelIds : undefined,
        assignee_id: assigneeId || undefined,
        project_id: projectId || undefined,
        estimate: estimate ? parseFloat(estimate) : undefined,
        due_date: dueDate || undefined,
        sync_close: syncClose,
      };
      const result = await api.sendTaskToLinear(task.id, payload);
      showToast(
        `Sent to Linear as ${result.linear_identifier || "new issue"} 🐾`,
        "normal",
      );
      onCreated?.(result);
      onClose();
    } catch (err) {
      showToast("Linear create failed: " + (err?.message || "unknown"), "high");
    }
    setSubmitting(false);
  };

  const activeCycleLabel = meta?.activeCycle
    ? `#${meta.activeCycle.number} (active) — ${formatCycleDates(meta.activeCycle)}`
    : null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="linear-create-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <Send size={14} />
          <span>Send to Linear</span>
          {meta?.key && meta?.name && (
            <span className="linear-team-chip">{meta.key} · {meta.name}</span>
          )}
          <button
            type="button"
            className="btn btn-sm"
            onClick={onClose}
            style={{ marginLeft: "auto" }}
          >
            <X size={10} />
          </button>
        </div>

        {loadingMeta ? (
          <div className="linear-modal-loading">
            <Loader size={20} className="spin" />
            <span>Loading team states, labels, cycles…</span>
          </div>
        ) : metaError ? (
          <div className="linear-modal-error">
            <p>Couldn't load Linear team metadata.</p>
            <p><small>{metaError}</small></p>
            <p><small>Check Settings → Integrations → Linear for API key + team.</small></p>
          </div>
        ) : (
          <form className="modal-body linear-create-form" onSubmit={handleSubmit}>
            <div className="form-row">
              <label>
                Title <span className="required">*</span>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  autoFocus
                  required
                />
              </label>
            </div>

            <div className="form-row">
              <label>
                Description
                <textarea
                  rows={4}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Markdown OK"
                />
              </label>
            </div>

            <div className="form-row form-row-inline">
              <label>
                State
                <select value={stateId} onChange={(e) => setStateId(e.target.value)}>
                  {(meta.states || []).map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Priority
                <select value={priority} onChange={(e) => setPriority(e.target.value)}>
                  <option value="0">No priority</option>
                  <option value="1">Urgent</option>
                  <option value="2">High</option>
                  <option value="3">Medium</option>
                  <option value="4">Low</option>
                </select>
              </label>
              <label>
                Estimate
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  value={estimate}
                  onChange={(e) => setEstimate(e.target.value)}
                  placeholder="points"
                />
              </label>
            </div>

            <div className="form-row form-row-inline">
              <label>
                Cycle
                <select value={cycleId} onChange={(e) => setCycleId(e.target.value)}>
                  <option value="">None</option>
                  {activeCycleLabel && (
                    <option value={meta.activeCycle.id}>{activeCycleLabel}</option>
                  )}
                  {(meta.upcomingCycles || []).map((c) => (
                    <option key={c.id} value={c.id}>
                      #{c.number} (upcoming) — {formatCycleDates(c)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Assignee
                <select value={assigneeId} onChange={(e) => setAssigneeId(e.target.value)}>
                  <option value="">Unassigned</option>
                  {(meta.members || []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.displayName || m.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Due date
                <input
                  type="date"
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                />
              </label>
            </div>

            <div className="form-row">
              <label>
                Project
                <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                  <option value="">None</option>
                  {(meta.projects || []).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {(meta.labels || []).length > 0 && (
              <div className="form-row">
                <div className="form-label">Labels</div>
                <div className="linear-label-picker">
                  {(meta.labels || []).map((l) => (
                    <label
                      key={l.id}
                      className={`linear-label-chip${labelIds.includes(l.id) ? " selected" : ""}`}
                      style={l.color ? { "--label-color": l.color } : undefined}
                    >
                      <input
                        type="checkbox"
                        checked={labelIds.includes(l.id)}
                        onChange={() => toggleLabel(l.id)}
                      />
                      <span>{l.name}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            <label className="linear-sync-close">
              <input
                type="checkbox"
                checked={syncClose}
                onChange={(e) => setSyncClose(e.target.checked)}
              />
              <span>
                Close the Linear issue when this task closes
                <small>
                  Off by default — when on, marking this task done or
                  cancelled in Maiko pushes the matching state to
                  Linear. Leave off while testing.
                </small>
              </span>
            </label>

            <div className="form-actions">
              <button type="button" className="btn" onClick={onClose}>
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={submitting || !title.trim()}
              >
                {submitting
                  ? <><Loader size={10} className="spin" /> Sending…</>
                  : <><Send size={10} /> Send to Linear</>}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function formatCycleDates(c) {
  if (!c?.startsAt) return "";
  try {
    const s = new Date(c.startsAt);
    const e = new Date(c.endsAt);
    const opts = { month: "short", day: "numeric" };
    return `${s.toLocaleDateString(undefined, opts)}–${e.toLocaleDateString(undefined, opts)}`;
  } catch {
    return "";
  }
}
