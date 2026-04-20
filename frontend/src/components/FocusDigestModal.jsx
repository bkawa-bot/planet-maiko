import { AlertCircle, Clock, X } from "lucide-react";
import { relativeTime } from "../utils/dates";
import "./FocusDigestModal.css";

/**
 * Shown when the user flips from a focus state (soft / deep / away)
 * back to available, if anything was held while they were gone.
 *
 * Captures the "welcome back" moment — without this, the release is
 * silent (held pupdates just quietly reappear in the inbox).
 *
 * The digest is captured BEFORE the state flip, because
 * set_state("available") strips the held flag from every held pupdate
 * on the server side. Calling /focus/digest after the flip would
 * return an empty list.
 *
 * Props:
 *   digest — { needs_attention: [...], can_wait: [...], total_held: N }
 *   onClose — () => void
 */
export default function FocusDigestModal({ digest, onClose }) {
  if (!digest || digest.total_held === 0) return null;

  const { needs_attention = [], can_wait = [], total_held } = digest;

  return (
    <div className="modal-overlay focus-digest-overlay" onClick={onClose}>
      <div className="focus-digest-modal" onClick={(e) => e.stopPropagation()}>
        <div className="focus-digest-header">
          <div>
            <div className="focus-digest-welcome">Welcome back 🐾</div>
            <div className="focus-digest-sub">
              {total_held === 1 ? "1 thing" : `${total_held} things`} came in while you were away.
              They're all in your inbox now.
            </div>
          </div>
          <button className="focus-digest-close" onClick={onClose} aria-label="Close">
            <X size={14} />
          </button>
        </div>

        {needs_attention.length > 0 && (
          <div className="focus-digest-section">
            <div className="focus-digest-section-label">
              <AlertCircle size={11} /> Worth a look
              <span className="focus-digest-count">{needs_attention.length}</span>
            </div>
            <ul className="focus-digest-list">
              {needs_attention.map((item) => (
                <li key={item.id} className="focus-digest-item">
                  <span className={`focus-digest-priority priority-${item.effective_priority}`}>
                    {item.effective_priority}
                  </span>
                  <span className="focus-digest-title" title={item.title}>
                    {item.title}
                  </span>
                  {item.timestamp && (
                    <span className="focus-digest-time">{relativeTime(item.timestamp)}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {can_wait.length > 0 && (
          <div className="focus-digest-section focus-digest-can-wait">
            <div className="focus-digest-section-label">
              <Clock size={11} /> Can wait
              <span className="focus-digest-count">{can_wait.length}</span>
            </div>
            <div className="focus-digest-can-wait-note">
              Low-priority items — they're in your inbox whenever you want them.
            </div>
          </div>
        )}

        <div className="focus-digest-footer">
          <button className="btn btn-primary" onClick={onClose}>
            Got it
          </button>
        </div>
      </div>
    </div>
  );
}
