import { Loader, Layers, CheckCircle2, AlertCircle } from "lucide-react";
import "./BackfillProgress.css";

/**
 * Progress panel for the manual cluster-duplicates sweep. Mirrors
 * BackfillProgress's shape so the visual language stays consistent —
 * a header with icon + label, a one-line detail, the gradient bar,
 * and a stats footer.
 *
 * Expects /api/learnings/cluster/status response:
 *   { running, current_category, processed, total, started_at,
 *     finished_at, result: { categories_scanned, learnings_merged,
 *     clusters_processed, skipped }, error }
 *
 * Reuses .backfill-progress* classes so we don't fork a near-
 * identical stylesheet — the UX is the same shape, only the
 * semantics differ.
 */
export default function ClusterProgress({ progress }) {
  if (!progress) return null;

  const { running, current_category, processed = 0, total = 0, result, error } = progress;
  const finished = !running && (result || error);

  let phase = "running";
  if (error) phase = "error";
  else if (finished) phase = "done";

  const Icon = phase === "done" ? CheckCircle2
    : phase === "error" ? AlertCircle
    : Layers;

  // Bar fill: the cluster pass walks total Learnings across all
  // categories, processed counts those it has touched. Indeterminate
  // when total is still 0 (haven't reached the first category yet).
  let percent = null;
  if (total > 0) {
    percent = Math.min(100, Math.round((processed / total) * 100));
  }

  const merged = result?.learnings_merged ?? 0;
  const scanned = result?.categories_scanned ?? 0;

  return (
    <div className={`backfill-progress ${phase}`}>
      <div className="backfill-progress-head">
        <Icon
          size={14}
          className={running ? "spin" : ""}
          style={{ flexShrink: 0 }}
        />
        <div className="backfill-progress-title">
          {phase === "done" ? "Clustering complete"
            : phase === "error" ? "Clustering failed"
            : "Merging duplicate rules"}
        </div>
        {total > 0 && running && (
          <div className="backfill-progress-repos">
            {processed} / {total}
          </div>
        )}
      </div>

      {running && current_category && (
        <div className="backfill-progress-detail">
          Asking Claude to merge rules in <code>{current_category}</code>
        </div>
      )}
      {running && !current_category && (
        <div className="backfill-progress-detail">
          Loading rules to compare…
        </div>
      )}
      {phase === "done" && (
        <div className="backfill-progress-detail success">
          {merged === 0
            ? `No duplicates found across ${scanned} categor${scanned === 1 ? "y" : "ies"}.`
            : `Merged ${merged} duplicate${merged === 1 ? "" : "s"} across ${scanned} categor${scanned === 1 ? "y" : "ies"}.`}
        </div>
      )}
      {phase === "error" && (
        <div className="backfill-progress-detail danger">{error}</div>
      )}

      <div className="backfill-progress-bar">
        <div
          className={`backfill-progress-bar-fill ${percent == null ? "indeterminate" : ""}`}
          style={percent != null ? { width: `${percent}%` } : undefined}
        />
      </div>

      {(running || phase === "done") && total > 0 && (
        <div className="backfill-progress-stats">
          <span><strong>{processed}</strong>/{total} rules processed</span>
          {result && (
            <>
              <span>·</span>
              <span><strong>{merged}</strong> merged</span>
              <span>·</span>
              <span><strong>{scanned}</strong> categories scanned</span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
