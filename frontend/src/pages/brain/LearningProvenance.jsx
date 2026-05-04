import { Loader } from "lucide-react";
import { formatRepo } from "../../utils/repo";



/**
 * Provenance drill-down for a learning. Shows the raw signals that
 * produced it — which PR comment, which reviewer, which file. Fetched
 * lazily the first time a learning is expanded.
 *
 * For PR-comment signals we reconstruct the inline-review permalink
 * (`.../pull/<n>#discussion_r<comment_id>`) from examples[].pr_number
 * plus signal.external_id. Non-PR signals (agent discovery, manual)
 * just render their text and source tag.
 */
export default function LearningProvenance({ loading, signals, defaultOrg }) {
  if (loading) {
    return (
      <div className="learning-provenance">
        <Loader size={10} className="spin" /> Loading signals…
      </div>
    );
  }
  if (!signals || signals.length === 0) {
    return (
      <div className="learning-provenance learning-provenance-empty">
        No signals linked to this learning yet.
      </div>
    );
  }
  return (
    <div className="learning-provenance">
      {signals.map((s) => {
        const examples = Array.isArray(s.examples) ? s.examples : [];
        const primary = examples[0] || {};
        const permalink = (s.source_type === "pr_comment" && s.repo && primary.pr_number && s.external_id)
          ? `https://github.com/${s.repo}/pull/${primary.pr_number}#discussion_r${s.external_id}`
          : null;
        return (
          <div key={s.id} className="provenance-signal">
            <div className="provenance-header">
              <span className="provenance-source">{s.source_type}</span>
              {s.reviewer && <span className="provenance-reviewer">@{s.reviewer}</span>}
              {s.repo && (
                <span className="provenance-repo" title={s.repo}>
                  {formatRepo(s.repo, defaultOrg)}
                </span>
              )}
              {s.severity && s.severity !== "suggestion" && (
                <span className={`provenance-severity sev-${s.severity}`}>{s.severity}</span>
              )}
              {permalink && (
                <a
                  className="provenance-link"
                  href={permalink}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open on GitHub"
                >
                  ↗
                </a>
              )}
            </div>
            {/* Prefer the raw comment body so the user sees exactly
                what a reviewer actually wrote. Fall back to the
                cleaned rule text when we don't have the original
                (pre-column signals from before the migration, or
                agent/manual signals that never had a "raw" form). */}
            <div className="provenance-text">{s.original_text || s.text}</div>
            {s.original_text && s.text && s.original_text !== s.text && (
              <div className="provenance-rule" title="LLM-cleaned rule summary">
                ↳ rule: {s.text}
              </div>
            )}
            {examples.length > 0 && (
              <div className="provenance-examples">
                {examples.map((ex, i) => (
                  <div key={i} className="provenance-example">
                    {ex.path && <span className="provenance-path">{ex.path}</span>}
                    {ex.pr_number && <span className="provenance-pr">PR #{ex.pr_number}</span>}
                    {ex.author && <span className="provenance-author">by @{ex.author}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


