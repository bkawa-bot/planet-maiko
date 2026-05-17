import { useState } from "react";
import { HelpCircle, X } from "@icons";
import ModalPortal from "./ModalPortal";

export default function InfoButton({ title, children }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button className="btn-info-trigger" onClick={() => setOpen(true)} title="What is this?">
        <HelpCircle size={14} />
      </button>
      {open && (
        <ModalPortal>
        <div className="modal-overlay" onClick={() => setOpen(false)}>
          <div className="info-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              {title}
              <span style={{ flex: 1 }} />
              <button className="btn btn-sm" onClick={() => setOpen(false)} style={{ border: "none", padding: 4 }}><X size={14} /></button>
            </div>
            <div className="modal-body info-modal-body">
              {children}
            </div>
          </div>
        </div>
        </ModalPortal>
      )}
    </>
  );
}
