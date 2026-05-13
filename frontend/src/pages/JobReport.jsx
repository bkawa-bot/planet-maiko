import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, FileText } from "@icons";
import { api } from "../api/client";
import { renderMarkdown } from "../utils/markdown";
import PlanetSpinner from "../components/PlanetSpinner";
import AgentChatThread from "../components/AgentChatThread";
import "./ReviewPlan.css";

/**
 * Unified report viewer for any AgentJob result that ISN'T a code
 * review — investigation findings, cartograph walks, skill outputs,
 * pack-owned one-shot runs.
 *
 * Code reviews keep their own diff-with-comments page (ReviewDiff.jsx)
 * because the diff + inline comments rail is a different shape than
 * "markdown report + chat thread".
 *
 * Reads the AgentJob row directly (job.artifact carries the markdown
 * output for jobs that finished via the lightweight specialty path,
 * and the existing AgentJob reply handler mirrors task-linked
 * artifacts onto the job too). The chat thread shares the same
 * inbox/messages endpoints as task-keyed agents — AgentMessage.task_id
 * holds either a task id or a job id depending on which the agent
 * reported with.
 */
export default function JobReport() {
  const { jobId } = useParams();
  const navigate = useNavigate();

  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchJob = useCallback(async () => {
    setLoading(true);
    try {
      const j = await api.getAgentJob(jobId);
      setJob(j);
    } catch {
      setJob(null);
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => { fetchJob(); }, [fetchJob]);

  if (loading) {
    return (
      <div className="review-plan-page">
        <div className="review-plan-header">
          <PlanetSpinner size={14} /> Loading report…
        </div>
      </div>
    );
  }

  const artifact = job?.artifact;
  const hasArtifact = !!(artifact && artifact.trim());

  return (
    <div className="review-plan-page">
      <div className="review-plan-header">
        <button className="btn btn-sm" onClick={() => navigate(-1)}>
          <ArrowLeft size={10} /> Back
        </button>
        <div className="review-plan-title">
          <FileText size={14} /> {job?.title || `Job ${jobId}`}
          {job?.kind && <span className="review-plan-status approved">{job.kind}</span>}
          {job?.status && job.status !== "done" && (
            <span className="review-plan-status">{job.status}</span>
          )}
        </div>
      </div>

      {!hasArtifact ? (
        <div className="review-plan-empty">
          <p>
            {job?.status === "running"
              ? "Agent is still working — report will appear here when they finish."
              : job?.status === "failed"
                ? `Job failed${job?.error ? `: ${job.error}` : "."}`
                : "No report on this job yet."}
          </p>
        </div>
      ) : (
        <div className="review-plan-body">
          <div
            className="review-plan-content"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(artifact) }}
          />
        </div>
      )}

      <AgentChatThread
        id={jobId}
        hint="For follow-up questions and clarifications."
        emptyMessage="No messages yet. Ask the agent a follow-up — they'll respond on their next check-in."
      />
    </div>
  );
}
