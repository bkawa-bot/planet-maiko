import { useState } from "react";
import { ChevronDown, ChevronRight } from "@icons";

/**
 * Home Overview — user-configurable add-on for the rolling LLM
 * overview pane, plus the workday-end and interruption-budget knobs
 * that shape its tone.
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

          <label style={{ display: "block", marginTop: 16 }}>
            <div style={{ marginBottom: 6, fontSize: 12, color: "var(--text-muted)" }}>
              Workday ends around — for the "enough for today" card
            </div>
            <select
              value={config.user?.workday_end_hour ?? 17}
              onChange={(e) => {
                const v = e.target.value;
                updateField("user", "workday_end_hour", v === "off" ? null : parseInt(v, 10));
              }}
              style={{
                padding: 6, fontFamily: "inherit", fontSize: 13,
                background: "var(--bg-card)", color: "var(--text)",
                border: "1px solid var(--border)", borderRadius: "var(--radius-xs)",
              }}
            >
              <option value="off">Off — don't show the closing card</option>
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>
                  {h.toString().padStart(2, "0")}:00 local
                </option>
              ))}
            </select>
            <div className="setup-hint" style={{ marginTop: 6 }}>
              Maiko shows a warm "that's enough for today" card in the overview
              around this hour — 30 minutes before and for 2 hours after. Meant
              as permission to stop, not a cheer.
            </div>
          </label>

          <label style={{ display: "block", marginTop: 16 }}>
            <div style={{ marginBottom: 6, fontSize: 12, color: "var(--text-muted)" }}>
              Interruption budget — loud pupdates per day before Maiko softens the voice
            </div>
            <select
              value={config.user?.interruption_budget ?? 3}
              onChange={(e) => {
                const v = e.target.value;
                updateField("user", "interruption_budget", v === "off" ? null : parseInt(v, 10));
              }}
              style={{
                padding: 6, fontFamily: "inherit", fontSize: 13,
                background: "var(--bg-card)", color: "var(--text)",
                border: "1px solid var(--border)", borderRadius: "var(--radius-xs)",
              }}
            >
              <option value="off">Off — don't track interruptions</option>
              {[1, 2, 3, 4, 5, 7, 10].map((n) => (
                <option key={n} value={n}>{n} per day</option>
              ))}
            </select>
            <div className="setup-hint" style={{ marginTop: 6 }}>
              Once the count exceeds this budget, the overview leans toward
              "a lot piled up today — knock these out in one sitting" instead
              of surfacing each one as a fresh fire. Soft cap, not enforcement.
            </div>
          </label>

          <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 16 }}>
            <input
              type="checkbox"
              checked={!!config.user?.weekend_mode}
              onChange={(e) => updateField("user", "weekend_mode", e.target.checked)}
            />
            <span>Weekend mode — assume you're off-duty</span>
          </label>
          <div className="setup-hint" style={{ marginTop: 4, marginLeft: 22 }}>
            When on, agents read this from their TASK.md preamble and defer
            anything non-critical to the next workday. The Home overview
            also shifts toward "what can wait until Monday." Flip on Friday,
            flip off Monday.
          </div>
        </div>
      )}
    </section>
  );
}
