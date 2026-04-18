import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { X } from "lucide-react";


/**
 * Small popover that explains the footer "N pending" chip.
 *
 * The sum in the footer conflates three very different things —
 * unprocessed pupdates (short queue; brain cycle drains fast),
 * unsynthesized signals (long queue; LLM-gated, ok to have thousands
 * during a backfill), and pending learnings (user-gated). Showing
 * them individually with links gives the user somewhere to go and
 * removes the "why is it so high?" anxiety.
 */
export default function FooterPendingPopover({ pending, onClose }) {
  const navigate = useNavigate();
  const ref = useRef(null);

  useEffect(() => {
    const onClickAway = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, [onClose]);

  const pupdates = pending?.unprocessed_pupdates || 0;
  const signals = pending?.unclassified_signals || 0;
  const learnings = pending?.pending_learnings || 0;

  const go = (path) => {
    navigate(path);
    onClose();
  };

  return (
    <div className="footer-pending-popover" ref={ref}>
      <div className="footer-pending-header">
        <span>Pending in the brain</span>
        <button className="btn-ghost" onClick={onClose} title="Close">
          <X size={10} />
        </button>
      </div>

      <div className="footer-pending-row">
        <span className="footer-pending-count">{pupdates}</span>
        <div className="footer-pending-text">
          <div className="footer-pending-label">Unprocessed pupdates</div>
          <div className="footer-pending-hint">
            Waiting for brain-cycle triage (rules + LLM). Usually drains in
            under a cycle — nothing for you to do.
          </div>
        </div>
      </div>

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
  );
}
