import { Brain, X } from "@icons";
import ModalPortal from "../../components/ModalPortal";

/**
 * Read-only project-plan modal — shows the plan markdown body for a
 * project. Triggered from the project header's "Plan" button.
 */
export default function ViewPlanModal({ plan, onClose }) {
  if (!plan) return null;
  return (
    <ModalPortal>
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="info-modal"
        style={{ maxWidth: 650, maxHeight: "80vh" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <Brain size={14} /> Plan: {plan.title}
          <span style={{ flex: 1 }} />
          <button
            className="btn btn-sm"
            onClick={onClose}
            style={{ border: "none", padding: 4 }}
          >
            <X size={14} />
          </button>
        </div>
        <div className="modal-body" style={{ overflow: "auto" }}>
          <div
            className="md-content"
            style={{
              fontSize: 13,
              lineHeight: 1.7,
              color: "var(--text-dim)",
              whiteSpace: "pre-wrap",
            }}
          >
            {plan.description || "No plan generated yet. Click 'Plan' on the project header to generate one."}
          </div>
        </div>
      </div>
    </div>
    </ModalPortal>
  );
}
