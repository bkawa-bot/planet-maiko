import { useEffect, useState } from "react";
import { AlertCircle } from "lucide-react";
import { api } from "../api/client";
import "./MissingToolBanner.css";

/**
 * First-run safety net. Without `claude` in PATH, agent kickoffs
 * silently return {success: false, error: "claude CLI not found"}
 * and the UI just looks broken — agents never start, nothing in
 * the logs the user will think to check. Surface this loudly at
 * the top of the app so new users understand what's wrong.
 *
 * Polls /system/health (which already reports tool availability)
 * every 60s. Self-hides as soon as claude becomes available —
 * no reload needed.
 */
const POLL_MS = 60_000;

export default function MissingToolBanner() {
  const [missing, setMissing] = useState(null);

  useEffect(() => {
    const check = async () => {
      try {
        const h = await api.getSystemHealth();
        const tools = h?.tools || {};
        const gaps = [];
        if (tools.claude && !tools.claude.available) gaps.push("claude");
        if (tools.gh && !tools.gh.available) gaps.push("gh");
        if (tools.git && !tools.git.available) gaps.push("git");
        setMissing(gaps);
      } catch {
        // Server unreachable — a separate problem, different banner elsewhere.
      }
    };
    check();
    const id = setInterval(check, POLL_MS);
    return () => clearInterval(id);
  }, []);

  if (!missing || missing.length === 0) return null;

  // Claude is load-bearing; git/gh are optional until repo flows run.
  // Show the claude-specific copy when claude is missing (most common
  // case) and a softer heads-up for the others.
  if (missing.includes("claude")) {
    return (
      <div className="missing-tool-banner missing-tool-banner-critical">
        <AlertCircle size={14} />
        <div className="missing-tool-banner-body">
          <div className="missing-tool-banner-title">
            Claude Code CLI isn't installed yet
          </div>
          <div className="missing-tool-banner-detail">
            Agents can't start until <code>claude</code> is on your PATH.
            Install it from{" "}
            <a
              href="https://docs.claude.com/en/docs/claude-code/quickstart"
              target="_blank"
              rel="noreferrer"
            >
              docs.claude.com
            </a>
            , then reload — this banner clears on its own.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="missing-tool-banner missing-tool-banner-warn">
      <AlertCircle size={14} />
      <div className="missing-tool-banner-body">
        <div className="missing-tool-banner-title">
          Missing: {missing.join(", ")}
        </div>
        <div className="missing-tool-banner-detail">
          Some flows need {missing.join(" + ")} installed. Agents still work,
          but repo discovery and worktrees won't.
        </div>
      </div>
    </div>
  );
}
