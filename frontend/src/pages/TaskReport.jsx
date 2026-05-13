import { useEffect, useState } from "react";
import { useParams, Navigate, useNavigate } from "react-router-dom";
import { ArrowLeft, FileText } from "@icons";
import { api } from "../api/client";
import { renderMarkdown } from "../utils/markdown";
import PlanetSpinner from "../components/PlanetSpinner";
import "./ReviewPlan.css";

/**
 * Legacy task-keyed report route. New memos route directly to
 * /jobs/<id> (JobReport.jsx) — markdown render + chat in one place,
 * works for jobs with or without a linked Task.
 *
 * This page exists to keep older memos and bookmarks working:
 *   1. Look up the AgentJob linked to this task
 *   2. If one exists, redirect to /jobs/<id>
 *   3. If not, fall back to rendering task.extra.artifact
 *      (covers tasks created before the AgentJob path existed)
 */
export default function TaskReport() {
  const { taskId } = useParams();
  const navigate = useNavigate();

  const [resolved, setResolved] = useState(null);   // {redirectTo} | {task} | null
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      const [task, jobs] = await Promise.all([
        api.getTask(taskId).catch(() => null),
        api.getAgentJobs({ source_task_id: taskId, include_done: true }).catch(() => []),
      ]);
      if (cancelled) return;
      if (jobs && jobs.length > 0) {
        // Newest first by created_at desc — pick the most recent
        // job linked to this task. Multiple jobs can exist if the
        // user re-ran the task; the latest is what they want.
        setResolved({ redirectTo: `/jobs/${jobs[0].id}` });
      } else {
        setResolved({ task });
      }
      setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [taskId]);

  if (loading) {
    return (
      <div className="review-plan-page">
        <div className="review-plan-header">
          <PlanetSpinner size={14} /> Loading report…
        </div>
      </div>
    );
  }

  if (resolved?.redirectTo) {
    return <Navigate to={resolved.redirectTo} replace />;
  }

  const task = resolved?.task;
  const artifact = task?.extra?.artifact;

  return (
    <div className="review-plan-page">
      <div className="review-plan-header">
        <button className="btn btn-sm" onClick={() => navigate(-1)}>
          <ArrowLeft size={10} /> Back
        </button>
        <div className="review-plan-title">
          <FileText size={14} /> {task?.title || `Task ${taskId}`}
          {task?.type && <span className="review-plan-status approved">{task.type}</span>}
        </div>
      </div>

      {!artifact ? (
        <div className="review-plan-empty">
          <p>No report on this task yet.</p>
        </div>
      ) : (
        <div className="review-plan-body">
          <div
            className="review-plan-content"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(artifact) }}
          />
        </div>
      )}
    </div>
  );
}
