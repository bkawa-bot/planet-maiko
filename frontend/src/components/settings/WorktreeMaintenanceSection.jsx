import { useEffect, useState } from "react";
import { Trash2, RefreshCw } from "lucide-react";
import { api } from "../../api/client";

/**
 * Worktree maintenance — disk-cleanup controls for agent worktrees.
 *
 * Agent runs leave two kinds of working dirs on disk:
 *   - Git worktrees under <repo>/.maiko-worktrees/<branch>/
 *   - Scratch dirs under <data_dir>/scratch-worktrees/<job_id>/
 *
 * cleanup_task_worktree() removes them when a task closes cleanly, but
 * abandoned runs, crashed kickoffs, and pre-rename orphans accumulate.
 * This panel exposes the background sweep (daily, gated on the brain
 * cycle) and a "Sweep now" button for impatient cleanup.
 */
function formatBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

function formatAge(mtime) {
  if (!mtime) return "—";
  const ageMs = Date.now() - mtime * 1000;
  const days = Math.floor(ageMs / (24 * 3600 * 1000));
  if (days < 1) return "<1 day";
  if (days === 1) return "1 day";
  return `${days} days`;
}

export default function WorktreeMaintenanceSection({ config, setConfig }) {
  const cleanup = config.agents?.worktree_cleanup || {};
  const enabled = cleanup.enabled ?? true;
  const maxAgeDays = cleanup.max_age_days ?? 14;

  const [stats, setStats] = useState(null);
  const [loadingStats, setLoadingStats] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [sweepResult, setSweepResult] = useState(null);

  const update = (patch) => setConfig((c) => ({
    ...c,
    agents: {
      ...(c.agents || {}),
      worktree_cleanup: { ...(c.agents?.worktree_cleanup || {}), ...patch },
    },
  }));

  const refreshStats = async () => {
    setLoadingStats(true);
    try {
      setStats(await api.getWorktreeStats());
    } catch (e) {
      // Best-effort — leave stats null if the call fails.
    } finally {
      setLoadingStats(false);
    }
  };

  useEffect(() => { refreshStats(); }, []);

  const handleSweepNow = async () => {
    if (sweeping) return;
    const confirmed = window.confirm(
      `Sweep worktrees older than ${maxAgeDays} days? Only worktrees whose ` +
      `AgentJob is done, cancelled, or failed will be touched — active ` +
      `worktrees are never removed.`
    );
    if (!confirmed) return;
    setSweeping(true);
    setSweepResult(null);
    try {
      const result = await api.sweepWorktrees(maxAgeDays);
      setSweepResult(result);
      refreshStats();
    } catch (e) {
      setSweepResult({ error: e.message || "Sweep failed" });
    } finally {
      setSweeping(false);
    }
  };

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" style={{ cursor: "default" }}>
        <span>Worktree maintenance</span>
      </div>
      <div className="collapsible-body">
        <div className="integration-section">
          <div className="setup-hint">
            Agent runs create working directories on disk — git worktrees
            inside each repo and scratch dirs in Maiko's data folder. They
            normally clean themselves up when a task closes, but abandoned
            runs and crashed kickoffs accumulate. The sweep removes
            directories older than the chosen age whose agent job is in a
            terminal state (done / cancelled / failed). Active worktrees
            are never touched.
          </div>

          <div className="integration-fields">
            <label>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => update({ enabled: e.target.checked })}
              />
              Auto-sweep daily
            </label>
            <label style={{ opacity: enabled ? 1 : 0.5 }}>
              Remove worktrees older than
              <input
                type="number"
                min="1"
                max="365"
                disabled={!enabled}
                value={maxAgeDays}
                onChange={(e) => update({ max_age_days: parseInt(e.target.value) || 14 })}
                style={{ width: 80, marginLeft: 8, marginRight: 8 }}
              />
              days
            </label>
          </div>

          {/* Live stats — what's actually on disk right now. */}
          <div className="setup-hint" style={{ marginTop: 16 }}>
            <strong>Current state on disk:</strong>{" "}
            {loadingStats ? "loading…" : stats ? (
              <>
                {stats.total_count} worktree{stats.total_count === 1 ? "" : "s"}{" "}
                ({stats.scratch_count} scratch + {stats.git_count} git) ·{" "}
                {formatBytes(stats.total_bytes)} ·{" "}
                oldest: {formatAge(stats.oldest_mtime)}
              </>
            ) : "—"}
            <button
              className="btn btn-sm"
              onClick={refreshStats}
              disabled={loadingStats}
              style={{ marginLeft: 8 }}
              title="Recount worktrees on disk"
            >
              <RefreshCw size={10} />
            </button>
          </div>

          <div style={{ marginTop: 12, display: "flex", gap: 8, alignItems: "center" }}>
            <button
              className="btn btn-sm"
              onClick={handleSweepNow}
              disabled={sweeping}
            >
              <Trash2 size={10} /> {sweeping ? "Sweeping…" : "Sweep now"}
            </button>
            {sweepResult && !sweepResult.error && (
              <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                Removed {sweepResult.removed} of {sweepResult.scanned} ·{" "}
                freed {formatBytes(sweepResult.freed_bytes)} ·{" "}
                skipped {sweepResult.skipped_active} active + {sweepResult.skipped_recent} recent
              </span>
            )}
            {sweepResult?.error && (
              <span style={{ fontSize: 12, color: "var(--urgent)" }}>
                {sweepResult.error}
              </span>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
