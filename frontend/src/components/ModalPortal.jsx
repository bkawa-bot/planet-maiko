import { createPortal } from "react-dom";

/**
 * Renders its children into document.body via React Portal so the
 * modal escapes any `backdrop-filter`, `transform`, `filter`, or
 * `perspective` ancestor — all of which per spec create a new
 * containing block for `position: fixed` descendants and pin the
 * modal inside that ancestor instead of the viewport.
 *
 * `.frost-pane` (used by Agents, Tasks, Automations etc.) has
 * `backdrop-filter: blur(12px)` for its frosted look; without this
 * portal, any modal rendered inside those pages lands clipped to the
 * frost-pane's box (behind the top nav, inside a task card, etc.).
 *
 * Drop-in usage:
 *   <ModalPortal>
 *     <div className="modal-overlay" onClick={onClose}>
 *       <div className="my-modal" onClick={(e) => e.stopPropagation()}>…</div>
 *     </div>
 *   </ModalPortal>
 */
export default function ModalPortal({ children }) {
  if (typeof document === "undefined") return null;
  return createPortal(children, document.body);
}
