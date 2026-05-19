import { useState } from "react";
import { ChevronDown, ChevronRight } from "@icons";

/**
 * Home Overview — user-configurable add-on for the rolling LLM
 * overview pane.
 */
export default function HomeOverviewSection({ config, updateField }) {
  const [open, setOpen] = useState(false);

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>Home Overview</span>
      </div>
      {open && (
        <div className="collapsible-body">
          <div className="setup-hint">
            Maiko generates a warm daily overview for your Home page. You can give
            her an optional add-on instruction — anything you want her to also do
            when writing it. She has full tool access (Bash, WebFetch, your
            configured MCP servers), so she can actually go do these things.
          </div>
          <label style={{ display: "block", marginTop: 12 }}>
            <div style={{ marginBottom: 6, fontSize: 12, color: "var(--text-muted)" }}>
              Custom add-on instruction (optional)
            </div>
            <textarea
              style={{
                width: "100%", minHeight: 100, padding: 8,
                fontFamily: "inherit", fontSize: 13,
                background: "var(--bg-card)", color: "var(--text)",
                border: "1px solid var(--border)", borderRadius: "var(--radius-xs)",
                resize: "vertical",
              }}
              value={config.overview?.custom_prompt || ""}
              onChange={(e) => updateField("overview", "custom_prompt", e.target.value)}
              placeholder={`e.g. "please also search my Slack for overnight mentions in #core-team" or "remind me which PRs have been sitting for more than 48 hours"`}
            />
          </label>
          <div className="setup-hint" style={{ marginTop: 8 }}>
            Overview regenerates roughly every 4 hours, or you can click Refresh
            on the pane itself. Changes here take effect on the next generation.
          </div>
        </div>
      )}
    </section>
  );
}
