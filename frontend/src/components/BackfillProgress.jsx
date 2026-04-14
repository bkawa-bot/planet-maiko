import { Loader, Download, Sparkles, Layers, CheckCircle2, AlertCircle } from "lucide-react";
import "./BackfillProgress.css";

/**
 * Live progress panel for the async PR backfill job. Appears on the
 * Knowledge page while backfill is running. Shows the current phase,
 * the repo being scanned, PR counter within that repo, and the
 * running signal count.
 *
 * Expects the raw backfill status dict from
 * /api/learnings/backfill/status.
 */
const PHASE_LABEL = {
  fetching: "Fetching PR reviews",
  synthesizing: "Synthesizing comments into rules",
  aggregating: "Aggregating into learnings",
  clustering: "Merging duplicate rules",
  done: "Done",
  error: "Error",
  idle: "Starting...",
};

const PHASE_ICON = {
  fetching: Download,
  synthesizing: Sparkles,
  aggregating: Layers,
  clustering: Layers,
  done: CheckCircle2,
  error: AlertCircle,
};

export default function BackfillProgress({ progress }) {
  if (!progress) return null;
  const {
    phase = "idle",
    current_repo,
    comments_done = 0,
    comments_total = 0,
    signals_created = 0,
    repos_done = 0,
    repos_total = 0,
    error,
  } = progress;

  const Icon = PHASE_ICON[phase] || Loader;
  const isActive = phase !== "done" && phase !== "error";

  // Visual bar: inside "fetching" we have per-comment granularity once
  // the batch endpoint has told us the total; outside of that the bar
  // pulses indeterminately.
  let percent = null;
  if (phase === "fetching" && comments_total > 0) {
    const reposDenom = Math.max(repos_total, 1);
    const ratio = (repos_done + (comments_done / comments_total)) / reposDenom;
    percent = Math.min(100, Math.round(ratio * 100));
  }

  return (
    <div className={`backfill-progress ${phase}`}>
      <div className="backfill-progress-head">
        <Icon
          size={14}
          className={isActive ? "spin" : ""}
          style={{ flexShrink: 0 }}
        />
        <div className="backfill-progress-title">{PHASE_LABEL[phase] || phase}</div>
        {repos_total > 0 && (
          <div className="backfill-progress-repos">repo {repos_done + (phase === "fetching" ? 1 : 0)} / {repos_total}</div>
        )}
      </div>

      {phase === "fetching" && current_repo && (
        <div className="backfill-progress-detail">
          {comments_total > 0
            ? <>Scanning <code>{current_repo}</code> — {comments_done}/{comments_total} inline comments</>
            : <>Fetching inline comments for <code>{current_repo}</code>...</>}
        </div>
      )}
      {phase === "synthesizing" && (
        <div className="backfill-progress-detail">
          Sending {signals_created} comment(s) to the LLM for classification
        </div>
      )}
      {phase === "aggregating" && (
        <div className="backfill-progress-detail">
          Grouping similar signals into learnings
        </div>
      )}
      {phase === "clustering" && (
        <div className="backfill-progress-detail">
          Asking Claude to merge rules that say the same thing
        </div>
      )}
      {phase === "done" && (
        <div className="backfill-progress-detail success">
          Finished — {signals_created} signal(s) collected across {repos_done} repo(s)
        </div>
      )}
      {phase === "error" && error && (
        <div className="backfill-progress-detail danger">{error}</div>
      )}

      <div className="backfill-progress-bar">
        <div
          className={`backfill-progress-bar-fill ${percent == null ? "indeterminate" : ""}`}
          style={percent != null ? { width: `${percent}%` } : undefined}
        />
      </div>

      <div className="backfill-progress-stats">
        <span><strong>{signals_created}</strong> signals</span>
        <span>·</span>
        <span><strong>{repos_done}</strong>/{repos_total} repos complete</span>
        {phase === "fetching" && comments_total > 0 && (
          <>
            <span>·</span>
            <span><strong>{comments_done}</strong>/{comments_total} comments in {current_repo?.split("/").pop()}</span>
          </>
        )}
      </div>
    </div>
  );
}
