import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { relativeTime } from "../utils/dates";
import { Check, Heart, RefreshCw } from "lucide-react";
import "./PetLog.css";

/**
 * Pet Log — deployment-owner view of pets waiting for IRL delivery.
 *
 * For Brigitte specifically: when someone pets virtual Maiko, this is
 * where the receipt lives. Mark each pet "petted IRL" after you've
 * delivered it to the real dog; bulk-mark when you've done a group
 * pet session.
 *
 * Not behind auth — Maiko is an on-device personal tool and whoever
 * runs the deployment is the owner. The route is just less
 * discoverable than Home by design.
 */

export default function PetLog() {
  const [pets, setPets] = useState([]);
  const [counts, setCounts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showAcked, setShowAcked] = useState(false);
  const [busy, setBusy] = useState(false);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const [log, count] = await Promise.all([
        api.getPetLog({ limit: 200, unacked: showAcked ? "false" : "true" }),
        api.getPetCount(),
      ]);
      setPets(log || []);
      setCounts(count);
    } catch (err) {
      showToast(`Couldn't load pet log. ${err.message || ""}`, "high");
    }
    setLoading(false);
  };

  useEffect(() => { fetchAll(); /* eslint-disable-next-line */ }, [showAcked]);

  const markOne = async (id) => {
    if (busy) return;
    setBusy(true);
    try {
      await api.markPetIrl(id);
      setPets((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      showToast(err.message || "Couldn't mark that pet", "high");
    }
    setBusy(false);
  };

  const markAll = async () => {
    if (busy || pets.length === 0) return;
    if (!window.confirm(`Mark all ${pets.length} pending pets as delivered IRL?`)) return;
    setBusy(true);
    try {
      const r = await api.markAllPetsIrl();
      showToast(`Marked ${r.marked} as delivered — go give Maiko some love.`, "normal");
      fetchAll();
    } catch (err) {
      showToast(err.message || "Couldn't mark all", "high");
    }
    setBusy(false);
  };

  const today = counts?.global_today ?? counts?.today ?? 0;
  const lifetime = counts?.global_lifetime ?? counts?.lifetime ?? 0;
  const fromPack = typeof counts?.global_today === "number";

  return (
    <div className="pet-log">
      <header className="pet-log-header">
        <div className="pet-log-title">
          <Heart size={18} /> <span>Pet Log</span>
        </div>
        <button className="btn btn-sm" onClick={fetchAll} title="Refresh">
          <RefreshCw size={12} />
        </button>
      </header>

      <div className="pet-log-stats">
        <div className="pet-log-stat">
          <div className="pet-log-stat-value">{today}</div>
          <div className="pet-log-stat-label">{fromPack ? "pets from the pack today" : "pets today"}</div>
        </div>
        <div className="pet-log-stat">
          <div className="pet-log-stat-value">{lifetime}</div>
          <div className="pet-log-stat-label">lifetime</div>
        </div>
        <div className="pet-log-stat">
          <div className="pet-log-stat-value">{pets.length}</div>
          <div className="pet-log-stat-label">{showAcked ? "in view" : "awaiting IRL"}</div>
        </div>
      </div>

      <div className="pet-log-toolbar">
        <label className="pet-log-toggle">
          <input
            type="checkbox"
            checked={showAcked}
            onChange={(e) => setShowAcked(e.target.checked)}
          />
          Show already delivered
        </label>
        {pets.length > 0 && !showAcked && (
          <button
            className="btn btn-sm btn-primary"
            onClick={markAll}
            disabled={busy}
          >
            <Check size={12} /> Mark all as delivered
          </button>
        )}
      </div>

      <div className="pet-log-note">
        Someone petted the version of Maiko that lives on the screen. Each row here is a pet you owe the real Maiko.
        Deliver it, come back, check it off. No points. No score. Just a good girl getting the love.
      </div>

      {loading ? (
        <div className="pet-log-empty">Loading…</div>
      ) : pets.length === 0 ? (
        <div className="pet-log-empty">
          {showAcked
            ? "No pets yet. The counter lives on Home."
            : "Caught up — all pets delivered. Nice work."}
        </div>
      ) : (
        <ul className="pet-log-list">
          {pets.map((p) => (
            <li key={p.id} className={`pet-log-item ${p.marked_irl_at ? "acked" : ""}`}>
              <div className="pet-log-item-body">
                <div className="pet-log-item-who">{p.user_key || "anonymous"}</div>
                <div className="pet-log-item-when">
                  {relativeTime(p.created_at)}
                  {p.marked_irl_at && (
                    <span className="pet-log-item-acked-at">
                      {" "}· delivered {relativeTime(p.marked_irl_at)}
                    </span>
                  )}
                </div>
                {p.note && <div className="pet-log-item-note">"{p.note}"</div>}
              </div>
              {!p.marked_irl_at && (
                <button
                  className="btn btn-sm"
                  onClick={() => markOne(p.id)}
                  disabled={busy}
                  title="Mark this pet as delivered to IRL Maiko"
                >
                  <Check size={12} /> Delivered
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
