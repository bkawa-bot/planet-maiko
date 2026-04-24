import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, FileText, Loader } from "lucide-react";
import { api } from "../api/client";
import { renderMarkdown } from "../utils/markdown";
import "./ReviewPlan.css";

/**
 * Read-only artifact viewer for one-shot report tasks (investigation,
 * repo_analysis). They store their output on task.extra.artifact and
 * clean up their worktree on reply, so there's no diff to show —
 * just the markdown report.
 */
export default function TaskReport() {
  const { taskId } = useParams();
  const navigate = useNavigate();

  const [task, setTask] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchTask = useCallback(async () => {
    setLoading(true);
    try {
      const t = await api.getTask(taskId);
      setTask(t);
    } catch {
      setTask(null);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => { fetchTask(); }, [fetchTask]);

  if (loading) {
    return (
      <div className="review-plan-page">
        <div className="review-plan-header">
          <Loader className="spin" size={14} /> Loading report…
        </div>
      </div>
    );
  }

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
