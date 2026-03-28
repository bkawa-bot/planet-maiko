import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

const VIEW_KEYS = {
  "1": "/",
  "2": "/inbox",
  "3": "/tasks",
  "4": "/team",
  "5": "/brainstorm",
  "6": "/suggestions",
  "7": "/agents",
  "8": "/skills",
  "9": "/settings",
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

      // Escape → close modals, deselect
      if (e.key === "Escape") {
        // Close any open modal
        const overlay = document.querySelector(".modal-overlay");
        if (overlay) overlay.click();
      }
    };

    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [navigate]);
}
