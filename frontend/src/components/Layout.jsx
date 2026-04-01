import { useEffect, useRef } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import { showToast } from "./Toast";
import { api } from "../api/client";
import "./Layout.css";

function usePupdateWatcher() {
  const knownIds = useRef(new Set());
  const initialized = useRef(false);

  useEffect(() => {
    const check = async () => {
      try {
        const pupdates = await api.getPupdates();
        if (!initialized.current) {
          pupdates.forEach((p) => knownIds.current.add(p.id));
          initialized.current = true;
          return;
        }

        for (const p of pupdates) {
          if (!knownIds.current.has(p.id)) {
            knownIds.current.add(p.id);
            showToast(p.title, p.priority);
          }
        }
      } catch (err) { /* ignore */ }
    };

    check();
    const interval = setInterval(check, 15000);
    return () => clearInterval(interval);
  }, []);
}

export default function Layout() {
  usePupdateWatcher();

  return (
    <div className="layout">
      <Sidebar />
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
