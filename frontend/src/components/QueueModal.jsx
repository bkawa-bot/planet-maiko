import { useEffect, useState } from "react";
import { Inbox, Loader, RefreshCw, RadarSweep, X } from "@icons";
import { api } from "../api/client";
import { showToast } from "./Toast";
import ModalPortal from "./ModalPortal";
import { relativeTime } from "../utils/dates";
import "./QueueModal.css";

/**
 * Modal view of pupdates waiting for the brain cycle to route them.
 * Opened from the Brain widget's "Unprocessed pupdates" row. Grouped
 * by source so a backfill of fifty GitHub events reads as one group.
 * "Run cycle now" drains the queue immediately.
 */
export default function QueueModal({ onClose }) {
  const [pupdates, setPupdates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [cycling, setCycling] = useState(false);

  const fetchQueue = async () => {
    setLoading(true);
    try {
      const list = await api.getPupdates({ brain_processed: false, limit: 500 });
      setPupdates(list || []);
    } catch (err) {
      console.error("[queue] fetch failed", err);
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchQueue();
    const id = setInterval(fetchQueue, 10_000);
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => {
      clearInterval(id);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const handleCycle = async () => {
    if (cycling) return;
    setCycling(true);
    showToast("Brain's thinking...", "normal");
    try {
      await api.runBrainCycle();
      showToast("Queue drained as much as it could", "normal");
      await fetchQueue();
    } catch (err) {
      showToast("Cycle failed: " + (err.message || "unknown"), "high");
    } finally {
      setCycling(false);
    }
  };

  const bySource = {};
  for (const p of pupdates) {
    const key = p.source || "other";
    (bySource[key] = bySource[key] || []).push(p);
  }
  const sourceKeys = Object.keys(bySource).sort();

  return (
    <ModalPortal>
    <div className="modal-overlay" onClick={onClose}>
      <div className="queue-modal" onClick={(e) => e.stopPropagation()}>
        <div className="queue-modal-header">
          <div className="queue-modal-title">
            <Inbox size={13} /> Unprocessed pupdates
            {pupdates.length > 0 && <span className="queue-modal-count">{pupdates.length}</span>}
          </div>
          <div className="queue-modal-actions">
            <button className="btn btn-sm" onClick={fetchQueue} title="Refetch">
              <RefreshCw size={10} /> Refresh
            </button>
            <button className="btn btn-primary btn-sm" onClick={handleCycle} disabled={cycling}>
              {cycling ? <><Loader size={10} className="spin" /> Running…</> : <><RadarSweep size={10} /> Run cycle now</>}
            </button>
            <button className="btn-ghost" onClick={onClose} title="Close">
              <X size={12} />
            </button>
          </div>
        </div>

        <div className="queue-modal-body">
          {loading && pupdates.length === 0 ? (
            <div className="queue-modal-empty">
              <Loader size={20} className="spin" /> Looking at the queue…
            </div>
          ) : pupdates.length === 0 ? (
            <div className="queue-modal-empty">
              <Inbox size={32} />
              <div>Queue's empty — brain's caught up.</div>
            </div>
          ) : (
            sourceKeys.map((src) => (
              <div key={src} className="queue-group">
                <div className="queue-group-header">
                  <span className="queue-group-source">{src}</span>
                  <span className="queue-group-count">{bySource[src].length}</span>
                </div>
                <div className="queue-group-list">
                  {bySource[src].map((p) => (
                    <div key={p.id} className="queue-row">
                      <div className="queue-row-main">
                        <span className="queue-row-type">{p.type}</span>
                        <span className="queue-row-title">{p.title}</span>
                      </div>
                      <div className="queue-row-meta">
                        {p.priority && p.priority !== "normal" && (
                          <span className={`queue-row-priority queue-row-priority-${p.priority}`}>{p.priority}</span>
                        )}
                        {p.timestamp && (
                          <span className="queue-row-time">{relativeTime(p.timestamp)}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
    </ModalPortal>
  );
}
