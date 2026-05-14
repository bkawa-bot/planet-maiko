import { useEffect, useRef, useState, useCallback } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import WeatherOverlay from "./WeatherOverlay";
import ShutdownModal from "./ShutdownModal";
import MissingToolBanner from "./MissingToolBanner";
import { showToast } from "./Toast";
import { api } from "../api/client";
import "./Layout.css";
import "./ShutdownModal.css";

// Mirror a few scene-visibility config flags onto html attributes so CSS
// can opt out of the hill background (and future heavy visuals). Also
// fetches the scene context so the global WeatherOverlay can render
// weather + season across every page, not just Home. Polls at the same
// cadence as the pupdate watcher so Settings changes take effect
// without a manual refresh.
// Scene + config don't change minute-to-minute. The hill background is
// driven by a config flag (set in Settings, persists across sessions);
// scene context is weather + season + time-of-day, which only meaningfully
// shifts every few minutes at most. Polling every 15s was hammering
// /api/scene (which used to do an inline LLM call → constant timeouts)
// for no real UX benefit.
const SCENE_POLL_MS = 5 * 60 * 1000;

function useSceneVisibility() {
  const [scene, setScene] = useState(null);
  const [weatherEnabled, setWeatherEnabled] = useState(true);
  useEffect(() => {
    const apply = async () => {
      try {
        const [cfg, sc] = await Promise.all([
          api.getConfig(),
          api.getScene().catch(() => null),
        ]);
        const hillsOn = cfg?.scene?.show_hill_background !== false;
        document.documentElement.setAttribute("data-hills", hillsOn ? "on" : "off");
        setWeatherEnabled(cfg?.scene?.show_weather_overlay !== false);
        setScene(sc);
      } catch { /* ignore */ }
    };
    apply();
    const interval = setInterval(apply, SCENE_POLL_MS);
    return () => clearInterval(interval);
  }, []);
  return { scene, weatherEnabled };
}

// New pupdates come in via background pollers (5+ min cadence). Toast
// detection doesn't need to run faster than that — 15s was overkill
// and meant we hit /api/pupdates four times per minute on every page.
const PUPDATE_POLL_MS = 60 * 1000;

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
    const interval = setInterval(check, PUPDATE_POLL_MS);
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
  const { scene, weatherEnabled } = useSceneVisibility();
  usePupdateWatcher();
  const { showIdlePrompt, setShowIdlePrompt, resetActivity } = useIdleDetection();
  // Lifted out of Sidebar so the idle-timeout modal can open the same
  // cleanup flow as the power button — one code path, two entry points.
  const [showShutdown, setShowShutdown] = useState(false);

  const handleStillWorking = () => {
    resetActivity();
    setShowIdlePrompt(false);
  };

  const handleLetMaikoSleep = () => {
    // Dismiss the nudge and hand off to the real shutdown ritual. The
    // user gets the preview + confirm flow instead of a silent SIGTERM
    // so they can uncheck cleanup or cancel entirely.
    setShowIdlePrompt(false);
    setShowShutdown(true);
  };

  const skyLabel = weatherEnabled ? scene?.scene?.sky : null;

  return (
    <div className="layout">
      <div className="world-bg" aria-hidden="true" />
      {skyLabel && <div className={`sky-overlay sky-${skyLabel}`} aria-hidden="true" />}
      <WeatherOverlay scene={scene} enabled={weatherEnabled} />
      <Sidebar onOpenShutdown={() => setShowShutdown(true)} />
      <main className="main-content">
        <MissingToolBanner />
        <Outlet />
      </main>

      {showShutdown && <ShutdownModal onClose={() => setShowShutdown(false)} />}

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
              <button className="btn" onClick={handleLetMaikoSleep}>
                Let Maiko sleep
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
