import { useEffect, useState } from "react";
import { GitBranch, AlertTriangle, X, Check } from "@icons";

/**
 * Surfaced when an approve action fails because the agent job's
 * scope_repo couldn't be resolved to a local clone. Lets the user
 * pick from configured repos that DO have clones, or paste a
 * filesystem path manually.
 *
 * Props:
 *   payload   — the 422 response payload from /memos/<id>/approve:
 *               { scope_repo, kind, title, configured_repos:
 *                 [{repo, local_path}], repo_roots: [...], memo_id }
 *   onCancel  — () => void
 *   onConfirm — (repoPath: string) => Promise<void>; receives the
 *               picked path so the caller can re-fire approveMemo
 *               with { repo_path: ... }.
 */
export default function RepoPickerModal({ payload, onCancel, onConfirm }) {
  const choices = payload?.configured_repos || [];
  const [picked, setPicked] = useState(choices[0]?.local_path || "");
  const [manualPath, setManualPath] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Auto-pick the first configured choice once the modal opens —
  // gives the user a sensible default for the common case where
  // they just need to confirm the right clone.
  useEffect(() => {
    if (!picked && choices.length > 0) setPicked(choices[0].local_path);
  }, [choices.length, picked]);

  const handleConfirm = async () => {
    const path = (manualPath.trim() || picked).trim();
    if (!path || submitting) return;
    setSubmitting(true);
    try {
      await onConfirm(path);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="repo-picker-overlay" onClick={onCancel}>
      <div className="repo-picker-modal" onClick={(e) => e.stopPropagation()}>
        <div className="repo-picker-header">
          <AlertTriangle size={14} />
          <span>No local clone found</span>
          <button className="btn btn-sm" onClick={onCancel} aria-label="Close">
            <X size={11} />
          </button>
        </div>
        <div className="repo-picker-body">
          <p className="repo-picker-explainer">
            <code>{payload?.scope_repo || "this job"}</code> doesn't have a
            local clone in your configured roots
            {payload?.repo_roots?.length > 0 && (
              <> ({payload.repo_roots.join(", ")})</>
            )}
            . Pick a repo to run the agent against, or paste a path.
          </p>
          {choices.length > 0 && (
            <div className="repo-picker-section">
              <div className="repo-picker-label">Configured repos with clones</div>
              <ul className="repo-picker-list">
                {choices.map((c) => (
                  <li key={c.local_path} className="repo-picker-choice">
                    <label>
                      <input
                        type="radio"
                        name="repo-pick"
                        value={c.local_path}
                        checked={picked === c.local_path && !manualPath.trim()}
                        onChange={() => { setPicked(c.local_path); setManualPath(""); }}
                      />
                      <GitBranch size={11} />
                      <span className="repo-picker-name">{c.repo}</span>
                      <code className="repo-picker-path">{c.local_path}</code>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="repo-picker-section">
            <div className="repo-picker-label">Or paste a path</div>
            <input
              type="text"
              value={manualPath}
              onChange={(e) => setManualPath(e.target.value)}
              placeholder="/Users/you/src/some-repo"
              className="repo-picker-manual"
            />
          </div>
          {choices.length === 0 && !manualPath.trim() && (
            <div className="repo-picker-hint">
              No configured repos have a local clone. Add a clone via Settings →
              GitHub, or paste a filesystem path above.
            </div>
          )}
        </div>
        <div className="repo-picker-footer">
          <button className="btn" onClick={onCancel} disabled={submitting}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={handleConfirm}
            disabled={submitting || (!manualPath.trim() && !picked)}
          >
            <Check size={11} /> {submitting ? "Approving…" : "Approve"}
          </button>
        </div>
      </div>
    </div>
  );
}
