import { useState } from "react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { CombinationLock, Check, X, Pencil } from "@icons";
import ModalPortal from "./ModalPortal";
import { renderMarkdown } from "../utils/markdown";

// Read a flow approval gate's plan / tasks (rendered markdown) and decide
// without leaving Home: approve, reject, or request changes. "Request changes"
// sends feedback to the producer agent to revise; the gate then re-parks here
// with the revised output (the human side of the reviewer->coder loop).
export default function GateReviewModal({ item, onClose, onSettled }) {
  const [busy, setBusy] = useState(false);
  const [askingChanges, setAskingChanges] = useState(false);
  const [feedback, setFeedback] = useState("");

  const act = async (fn, ok, verb) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      if (item.memo_id) await api.dismissMemo(item.memo_id).catch(() => {});
      showToast(ok, "normal");
      onSettled?.();
      onClose?.();
    } catch (err) {
      showToast(`Couldn't ${verb}: ${err.message || "unknown"}`, "high");
      setBusy(false);
    }
  };

  const approve = () =>
    act(() => api.approveWorkflowNode(item.run_id, item.node_run_id), "Approved 🐾", "approve");
  const reject = () =>
    act(() => api.rejectWorkflowNode(item.run_id, item.node_run_id), "Rejected", "reject");
  const sendChanges = () => {
    if (!feedback.trim()) return;
    act(
      () => api.requestChangesWorkflowNode(item.run_id, item.node_run_id, feedback.trim()),
      "Sent back for changes 🐾",
      "send for changes",
    );
  };

  return (
    <ModalPortal>
      <div className="modal-overlay" onClick={() => !busy && onClose?.()}>
        <div className="agent-edit-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-header">
            <CombinationLock size={14} /> {item.title || "Flow gate"}
            <span style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={onClose} disabled={busy}>
              <X size={12} />
            </button>
          </div>

          <div className="modal-body agent-edit-body">
            {item.body ? (
              <div
                className="review-queue-description markdown"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(item.body) }}
              />
            ) : (
              <div className="agent-edit-hint">This step left no readable output.</div>
            )}
            {askingChanges && (
              <label className="agent-edit-full" style={{ marginTop: 12 }}>
                What should they change?
                <textarea
                  rows={4}
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder="The changes you want before this moves on. The agent revises and re-submits for your approval."
                  autoFocus
                />
              </label>
            )}
          </div>

          <div className="agent-edit-footer">
            <button className="btn btn-danger" onClick={reject} disabled={busy}>
              <X size={12} /> Reject
            </button>
            <span style={{ flex: 1 }} />
            {askingChanges ? (
              <>
                <button className="btn" onClick={() => setAskingChanges(false)} disabled={busy}>
                  Back
                </button>
                <button
                  className="btn btn-primary"
                  onClick={sendChanges}
                  disabled={busy || !feedback.trim()}
                >
                  <Pencil size={12} /> Send for changes
                </button>
              </>
            ) : (
              <>
                <button className="btn" onClick={() => setAskingChanges(true)} disabled={busy}>
                  <Pencil size={12} /> Request changes
                </button>
                <button className="btn btn-primary" onClick={approve} disabled={busy}>
                  <Check size={12} /> Approve
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </ModalPortal>
  );
}
