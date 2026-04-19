import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { relativeTime } from "../utils/dates";
import { Bell, ClipboardCheck, FileText, HelpCircle, Lightbulb, X } from "lucide-react";
import "./PackRequestsWidget.css";

/**
 * "Your pack needs you" — a lightweight, high-cadence view of active
 * agent requests. Bypasses the LLM-backed overview pane so it updates
 * promptly when a plan is waiting or a review is ready.
 *
 * Polls every 30s. Shows at most 5 items. Clicking a row routes to
 * the appropriate surface (plan/diff/task).
 */

const POLL_MS = 30_000;

// type → UI meta (icon + label + destination resolver). Keep in sync
// with OverviewPane's resolveAction so routing stays consistent.
const TYPE_META = {
  agent_plan_for_approval: {
    icon: ClipboardCheck,
    label: "has a plan",
    route: (p) => `/tasks/${p.metadata?.task_id}/plan`,
    tone: "plan",
  },
  agent_ready_for_review: {
    icon: FileText,
    label: "ready for review",
    route: (p) => `/tasks/${p.metadata?.task_id}/review`,
    tone: "review",
  },
  // Same event as agent_ready_for_review but emitted via the brain
  // cycle's safety-net synthesizer (when the agent's MCP reply path
  // didn't fire). Surface it the same way.
  pr_review_complete: {
    icon: FileText,
    label: "finished the review",
    route: (p) => `/tasks/${p.metadata?.task_id}/review`,
    tone: "review",
  },
  agent_stuck: {
    icon: HelpCircle,
    label: "is stuck",
    route: (p) => `/tasks/${p.metadata?.task_id}`,
    tone: "stuck",
  },
  agent_proposal: {
    icon: Lightbulb,
    label: "has an idea",
    route: () => "/",
    tone: "idea",
  },
};


export default function PackRequestsWidget() {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchRequests = async () => {
    try {
      const r = await api.getPackRequests();
      setRequests(Array.isArray(r) ? r : []);
    } catch {
      // Silent — widget degrades to "last known" rather than breaking.
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchRequests();
    const id = setInterval(fetchRequests, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const dismiss = async (id, e) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await api.dismissPupdate(id);
      setRequests((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      showToast(err.message || "Couldn't dismiss", "high");
    }
  };

  return (
    <div className="home-widget pack-requests-widget">
      <div className="widget-header">
        <Bell size={12} /> Requests from your pack
      </div>

      {loading ? (
        <div className="pack-requests-empty">…</div>
      ) : requests.length === 0 ? (
        <div className="pack-requests-empty">
          All quiet — nobody's waiting on you.
        </div>
      ) : (
        <ul className="pack-requests-list">
          {requests.slice(0, 5).map((p) => {
            const meta = TYPE_META[p.type] || null;
            if (!meta) return null;
            const Icon = meta.icon;
            const who = p.agent_name || "An agent";
            const where = meta.route(p);
            return (
              <li key={p.id} className={`pack-request pack-request-${meta.tone}`}>
                <Link to={where} className="pack-request-link">
                  <Icon size={11} className="pack-request-icon" />
                  <div className="pack-request-body">
                    <div className="pack-request-title">
                      <span className="pack-request-who">{who}</span>{" "}
                      <span className="pack-request-label">{meta.label}</span>
                    </div>
                    {p.timestamp && (
                      <div className="pack-request-time">{relativeTime(p.timestamp)}</div>
                    )}
                  </div>
                </Link>
                <button
                  className="pack-request-dismiss"
                  onClick={(e) => dismiss(p.id, e)}
                  title="Dismiss"
                  aria-label="Dismiss"
                >
                  <X size={10} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
