import { useEffect } from "react";
import { useParams, useNavigate, Navigate } from "react-router-dom";
import { api } from "../api/client";
import PlanetSpinner from "../components/PlanetSpinner";

/**
 * Bridge for legacy /tasks/:taskId/{review,plan,report} URLs.
 * Resolves the task's linked AgentJob and forwards to the unified
 * /jobs/:jobId page with ?view=<diff|plan|report>. Falls back to
 * /tasks/:taskId when no job exists (pre-unification task that
 * never got an AgentJob).
 *
 * Kept as its own component so the route still loads instantly even
 * if AgentJobPage is mid-fetch — and so cached bookmarks / older
 * memo CTAs continue to land on the right surface without the user
 * seeing a 404.
 */
export default function TaskRouteRedirect({ view = "diff" }) {
  const { taskId } = useParams();
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // List jobs scoped to this task; pick the most recent
        // non-cancelled one. Same resolution shape the backend uses
        // for canonicalizing inbox ids.
        const jobs = await api.getAgentJobs({ source_task_id: taskId, limit: 50 }).catch(() => []);
        if (cancelled) return;
        const live = (Array.isArray(jobs) ? jobs : [])
          .filter((j) => j.status !== "cancelled")
          .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
        const target = live[0];
        if (target) {
          navigate(`/jobs/${target.id}?view=${view}`, { replace: true });
        } else {
          // No job linked — drop the user back on the tasks page
          // rather than rendering a half-resolved review surface.
          navigate(`/tasks`, { replace: true });
        }
      } catch {
        if (!cancelled) navigate(`/tasks`, { replace: true });
      }
    })();
    return () => { cancelled = true; };
  }, [taskId, view, navigate]);

  return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>
      <PlanetSpinner size={14} /> Redirecting…
    </div>
  );
}
