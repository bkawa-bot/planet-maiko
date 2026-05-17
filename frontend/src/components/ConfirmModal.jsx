import { AlertTriangle, X, Loader } from "@icons";
import ModalPortal from "./ModalPortal";
import "./ConfirmModal.css";

/**
 * Confirmation modal for resource-intensive or otherwise irreversible
 * actions. Shows a warning triangle + title + body, then Cancel /
 * Confirm buttons. Primary button stays disabled while `busy` is true.
 *
 * Props:
 *   open         — bool; when false, nothing is rendered.
 *   title        — string, typically "This will take a while" or similar.
 *   body         — string | ReactNode; the "what will happen + why
 *                  you should care" message. ReactNode lets callers
 *                  include estimated-cost chips, links, etc.
 *   confirmText  — button label, defaults to "Run it".
 *   severity     — "warn" (default) | "danger". Styles the top banner.
 *   busy         — optional bool; shows a spinner and disables the
 *                  confirm button while an action is in flight.
 *   onConfirm    — () => void | Promise<void>. Callers handle the
 *                  busy transition themselves so the parent controls
 *                  close-on-success vs stay-open-on-error.
 *   onCancel     — () => void, closes the modal.
 */
export default function ConfirmModal({
  open,
  title = "This is a resource-intensive action",
  body,
  confirmText = "Run it",
  severity = "warn",
  busy = false,
  onConfirm,
  onCancel,
}) {
  if (!open) return null;
  return (
    <ModalPortal>
    <div className="modal-overlay" onClick={() => !busy && onCancel?.()}>
      <div className={`confirm-modal confirm-${severity}`} onClick={(e) => e.stopPropagation()}>
        <div className="confirm-banner">
          <AlertTriangle size={14} />
          <span>Heads up</span>
          <button
            className="confirm-close"
            onClick={() => !busy && onCancel?.()}
            disabled={busy}
            aria-label="Close"
          >
            <X size={12} />
          </button>
        </div>
        <div className="confirm-body">
          <div className="confirm-title">{title}</div>
          <div className="confirm-message">{body}</div>
        </div>
        <div className="confirm-actions">
          <button className="btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={onConfirm} disabled={busy}>
            {busy ? <><Loader size={12} className="spin" /> Starting...</> : confirmText}
          </button>
        </div>
      </div>
    </div>
    </ModalPortal>
  );
}
