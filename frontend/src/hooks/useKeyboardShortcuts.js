import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

const VIEW_KEYS = {
  "1": "/",
  "2": "/inbox",
  "3": "/tasks",
  "4": "/agents",
  "5": "/brain",
  "6": "/settings",
};

export default function useKeyboardShortcuts() {
  const navigate = useNavigate();

  useEffect(() => {
    const handler = (e) => {
      // Don't fire in inputs
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") {
        return;
      }

      // Number keys → navigate to view
      if (VIEW_KEYS[e.key]) {
        e.preventDefault();
        navigate(VIEW_KEYS[e.key]);
        return;
      }

      // / → focus search (if we add one later)
      if (e.key === "/") {
        e.preventDefault();
        const search = document.querySelector(".search-input");
        if (search) search.focus();
        return;
      }

      // j/k — navigate items in lists
      if (e.key === "j" || e.key === "k") {
        const cards = document.querySelectorAll(".pupdate-card, .task-card, .strategy-card");
        if (cards.length === 0) return;

        const current = document.querySelector(".pupdate-card.keyboard-focus, .task-card.keyboard-focus, .strategy-card.keyboard-focus");
        let idx = current ? Array.from(cards).indexOf(current) : -1;

        if (current) current.classList.remove("keyboard-focus");

        if (e.key === "j") idx = Math.min(idx + 1, cards.length - 1);
        else idx = Math.max(idx - 1, 0);

        cards[idx].classList.add("keyboard-focus");
        cards[idx].scrollIntoView({ block: "nearest" });
        e.preventDefault();
        return;
      }

      // d — dismiss focused item
      if (e.key === "d") {
        const focused = document.querySelector(".keyboard-focus");
        if (focused) {
          const dismissBtn = focused.querySelector(".btn-danger");
          if (dismissBtn) dismissBtn.click();
        }
        return;
      }

      // o — open focused item's URL
      if (e.key === "o") {
        const focused = document.querySelector(".keyboard-focus");
        if (focused) {
          const link = focused.querySelector("a[target='_blank']");
          if (link) window.open(link.href, "_blank");
        }
        return;
      }

      // Escape → close modals, deselect
      if (e.key === "Escape") {
        // Close any open modal
        const overlay = document.querySelector(".modal-overlay");
        if (overlay) overlay.click();
        // Also clear keyboard focus
        const focused = document.querySelector(".keyboard-focus");
        if (focused) focused.classList.remove("keyboard-focus");
      }
    };

    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [navigate]);
}
