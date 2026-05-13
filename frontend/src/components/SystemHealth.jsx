import { useEffect, useRef, useState } from "react";
import { Activity, Brain, Database, AlertCircle } from "@icons";
import { api } from "../api/client";
import { relativeTime } from "../utils/dates";
import "./SystemHealth.css";

const POLL_MS = 60_000;
const STALE_BACKUP_HOURS = 36;


export default function SystemHealth() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  const fetch = async () => {
    try {
      setHealth(await api.getSystemHealth());
      setError(false);
    } catch {
      setError(true);
    }
  };

  useEffect(() => {
    fetch();
    const interval = setInterval(fetch, POLL_MS);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const onClickAway = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    if (open) document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, [open]);

  const level = computeLevel(health, error);

  return (
    <div className={`system-health system-health-${level}`} ref={ref}>
      <button
        className="system-health-dot"
        onClick={() => setOpen((v) => !v)}
        title={healthLabel(level)}
      >
        <span className="system-health-inner" />
      </button>
      {open && (
        <div className="system-health-popover">
          <div className="system-health-heading">{healthLabel(level)}</div>
          {!health && <div className="system-health-note">Connection lost or still loading…</div>}
          {health && !health.scheduler_running && (
            <div className="system-health-note danger">
              <AlertCircle size={11} /> Scheduler isn't running. Pollers won't tick until it restarts.
            </div>
          )}
          {health && (
            <>
              <div className="system-health-row">
                <Brain size={11} />
                <span className="system-health-row-label">Brain cycle</span>
                <span className="system-health-row-value">
                  {health.last_brain_cycle ? relativeTime(health.last_brain_cycle) : "never"}
                </span>
              </div>
              <div className="system-health-row">
                <Database size={11} />
                <span className="system-health-row-label">Latest backup</span>
                <span className="system-health-row-value">
                  {health.latest_backup
                    ? relativeTime(health.latest_backup.created_at)
                    : "none yet"}
                </span>
              </div>
              <div className="system-health-section">Pollers</div>
              {Object.keys(health.pollers || {}).length === 0 ? (
                <div className="system-health-note">No pollers enabled.</div>
              ) : (
                Object.entries(health.pollers).map(([name, s]) => (
                  <PollerRow key={name} name={name} status={s} />
                ))
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}


function PollerRow({ name, status }) {
  const state = pollerState(status);
  return (
    <div className={`system-health-row system-health-poller state-${state}`}>
      <Activity size={11} />
      <span className="system-health-row-label">{name}</span>
      <span className="system-health-row-value" title={status.last_error || ""}>
        {state === "error"
          ? "errored"
          : state === "stale"
          ? "overdue"
          : status.last_success_at
          ? relativeTime(status.last_success_at)
          : "waiting…"}
      </span>
    </div>
  );
}


// --- Classification -----------------------------------------------------

function pollerState(s) {
  if (s.last_error) return "error";
  if (!s.last_run_at) return "waiting";
  // Stale = haven't run within 2× interval. Only fires for actual
  // configured pollers (interval_seconds > 0).
  const interval = (s.interval_seconds || 0) * 1000;
  if (interval > 0) {
    const last = Date.parse(s.last_run_at);
    if (Number.isFinite(last) && Date.now() - last > 2 * interval) return "stale";
  }
  return "ok";
}


function computeLevel(health, error) {
  if (error || !health) return "red";
  if (!health.scheduler_running) return "red";

  const pollers = Object.values(health.pollers || {});
  if (pollers.some((s) => s.last_error)) return "red";
  if (pollers.some((s) => pollerState(s) === "stale")) return "yellow";

  const bk = health.latest_backup;
  if (!bk) return "yellow";
  const bkAge = Date.now() - Date.parse(bk.created_at);
  if (bkAge > STALE_BACKUP_HOURS * 3600_000) return "yellow";

  return "green";
}


function healthLabel(level) {
  if (level === "green") return "All systems calm";
  if (level === "yellow") return "Something is overdue";
  return "Something's wrong";
}
