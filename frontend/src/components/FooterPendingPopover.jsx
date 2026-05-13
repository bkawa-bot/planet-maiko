import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { X } from "@icons";
import QueueModal from "./QueueModal";


/**
 * Small popover that explains the footer "N pending" chip.
 *
 * The sum in the footer conflates three very different things —
 * unprocessed pupdates (short queue; brain cycle drains fast),
 * unsynthesized signals (long queue; LLM-gated, ok to have thousands
 * during a backfill), and pending learnings (user-gated). Showing
 * them individually with links gives the user somewhere to go and
 * removes the "why is it so high?" anxiety.
 *
 * Unprocessed pupdates opens an inline modal (drain + peek-at-queue)
 * rather than navigating — the queue is ephemeral, so sending the
 * user to another page just to glance at it was overkill.
 */
export default function FooterPendingPopover({ pending, onClose }) {
  const navigate = useNavigate();
  const ref = useRef(null);
  const [showQueue, setShowQueue] = useState(false);

  useEffect(() => {
    const onClickAway = (e) => {
      if (showQueue) return;
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, [onClose, showQueue]);

  const pupdates = pending?.unprocessed_pupdates || 0;
  const signals = pending?.unclassified_signals || 0;
  const learnings = pending?.pending_learnings || 0;

  const go = (path) => {
    navigate(path);
    onClose();
  };

  return (
    <>
      <div className="footer-pending-popover" ref={ref}>
        <div className="footer-pending-header">
          <span>Pending in the brain</span>
          <button className="btn-ghost" onClick={onClose} title="Close">
            <X size={10} />
          </button>
        </div>

        <button className="footer-pending-row" onClick={() => setShowQueue(true)}>
          <span className="footer-pending-count">{pupdates}</span>
          <div className="footer-pending-text">
            <div className="footer-pending-label">Unprocessed pupdates</div>
            <div className="footer-pending-hint">
              Waiting for brain-cycle triage (rules + LLM). Usually drains in
              under a cycle — click to peek or run one now.
            </div>
          </div>
        </button>

        <button className="footer-pending-row" onClick={() => go("/knowledge")}>
          <span className="footer-pending-count">{signals}</span>
          <div className="footer-pending-text">
            <div className="footer-pending-label">Unsynthesized signals</div>
            <div className="footer-pending-hint">
              Raw PR-comment signals waiting for LLM category synthesis. Large
              numbers here are normal after a backfill — the clustering phase
              drains one batch per cycle. Click for the Unsynthesized tab.
            </div>
          </div>
        </button>

        <button className="footer-pending-row" onClick={() => go("/knowledge")}>
          <span className="footer-pending-count">{learnings}</span>
          <div className="footer-pending-text">
            <div className="footer-pending-label">Pending learnings</div>
            <div className="footer-pending-hint">
              Clustered rules awaiting your review — this one <em>is</em> on you.
              Click to approve or dismiss.
            </div>
          </div>
        </button>
      </div>
      {showQueue && <QueueModal onClose={() => setShowQueue(false)} />}
    </>
  );
}
