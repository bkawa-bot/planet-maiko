import { useEffect, useRef, useState, useCallback } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import Footer from "./Footer";
import { showToast } from "./Toast";
import { api } from "../api/client";
import "./Layout.css";

// Mirror a few scene-visibility config flags onto html attributes so CSS
// can opt out of the hill background (and future heavy visuals). Polls at
// the same cadence as the pupdate watcher so Settings changes take effect
// without a manual refresh.
function useSceneVisibility() {
  useEffect(() => {
    const apply = async () => {
      try {
        const cfg = await api.getConfig();
        const hillsOn = cfg?.scene?.show_hill_background !== false;
        document.documentElement.setAttribute("data-hills", hillsOn ? "on" : "off");
      } catch { /* ignore */ }
    };
    apply();
    const interval = setInterval(apply, 15000);
    return () => clearInterval(interval);
  }, []);
}

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

const IDLE_TIMEOUT_MS = 2 * 60 * 60 * 1000; // 2 hours
const IDLE_CHECK_MS = 60 * 1000; // check every minute

function useIdleDetection() {
  const lastActivity = useRef(Date.now());
  const [showIdlePrompt, setShowIdlePrompt] = useState(false);
  const prompted = useRef(false);

  const resetActivity = useCallback(() => {
    lastActivity.current = Date.now();
    if (prompted.current) {
      prompted.current = false;
      setShowIdlePrompt(false);
    }
  }, []);

  useEffect(() => {
    const events = ["mousedown", "keydown", "scroll", "touchstart"];
    events.forEach((e) => window.addEventListener(e, resetActivity));
    return () => events.forEach((e) => window.removeEventListener(e, resetActivity));
  }, [resetActivity]);

  useEffect(() => {
    const check = () => {
      const idle = Date.now() - lastActivity.current;
      if (idle >= IDLE_TIMEOUT_MS && !prompted.current) {
        prompted.current = true;
        setShowIdlePrompt(true);
      }
    };
    const interval = setInterval(check, IDLE_CHECK_MS);
    return () => clearInterval(interval);
  }, []);

  return { showIdlePrompt, setShowIdlePrompt, resetActivity };
}

export default function Layout() {
  useSceneVisibility();
  usePupdateWatcher();
  const { showIdlePrompt, setShowIdlePrompt, resetActivity } = useIdleDetection();

  const handleStillWorking = () => {
    resetActivity();
    setShowIdlePrompt(false);
  };

  const handleShutdown = async () => {
    try {
      await api.shutdown();
    } catch (e) {}
    setShowIdlePrompt(false);
    document.title = "Planet Maiko (stopped)";
    showToast("Maiko is going to sleep. Restart with: maiko serve", "normal");
  };

  return (
    <div className="layout">
      <Sidebar />
      <main className="main-content">
        <Outlet />
      </main>
      <Footer />

      {showIdlePrompt && (
        <div className="modal-overlay">
          <div className="idle-modal">
            <div style={{ fontSize: 32, textAlign: "center", marginBottom: 12 }}>🐕💤</div>
            <h3 style={{ textAlign: "center", marginBottom: 8 }}>Still working?</h3>
            <p style={{ textAlign: "center", fontSize: 13, color: "var(--text-dim)", marginBottom: 16 }}>
              Maiko hasn't seen any activity for 2 hours. Want to keep the server running, or let Maiko sleep?
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
              <button className="btn btn-primary" onClick={handleStillWorking}>
                I'm still here!
              </button>
              <button className="btn" onClick={handleShutdown}>
                Let Maiko sleep
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
