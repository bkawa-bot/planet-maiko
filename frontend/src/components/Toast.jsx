import { useState, useEffect, useCallback } from "react";
import "./Toast.css";

let _addToast = null;

export function showToast(message, priority = "normal") {
  if (_addToast) _addToast({ message, priority, id: Date.now() });
}

export default function ToastContainer() {
  const [toasts, setToasts] = useState([]);

  _addToast = useCallback((toast) => {
    setToasts((prev) => [...prev.slice(-2), toast]); // max 3
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== toast.id));
    }, 5000);
  }, []);

  return (
    <div className="toast-container">
      {toasts.map((t) => (
        <div key={t.id} className={`toast priority-${t.priority}`}>
          <span className={`toast-dot ${t.priority}`} />
          <span className="toast-message">{t.message}</span>
          <button className="toast-close" onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}>
            &times;
          </button>
        </div>
      ))}
    </div>
  );
}
