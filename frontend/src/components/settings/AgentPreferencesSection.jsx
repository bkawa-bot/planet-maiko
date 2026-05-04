import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

/**
 * Agent Preferences — role-specific instructions (reviewer/coder/
 * investigator), the legacy global custom instructions, branch
 * prefix, and the pre-approved tools list for Claude Code sessions.
 */
export default function AgentPreferencesSection({
  config, setConfig, updateField, updateRoleInstructions,
}) {
  const [open, setOpen] = useState(false);

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>Agent Preferences</span>
      </div>
      {open && (
        <div className="collapsible-body">
          <div className="integration-section">
            <div className="setup-hint">
              <strong>Role instructions</strong> apply team-wide to every agent of a given role.
              They get injected after the built-in role protocol and before each agent's own
              personality, so you can say "every reviewer cares about accessibility" once instead
              of editing every agent. Markdown is fine.
            </div>
            <div className="integration-fields">
              <label>
                Coder instructions
                <textarea
                  rows={4}
                  value={config.agents?.role_instructions?.coding || ""}
                  onChange={(e) => updateRoleInstructions("coding", e.target.value)}
                  placeholder={"e.g.\nAlways run tests before opening a PR.\nPrefer existing utilities in src/utils/ over adding new deps.\nNever commit TODO comments without an issue link."}
                  style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                />
              </label>
              <label>
                Reviewer instructions
                <textarea
                  rows={4}
                  value={config.agents?.role_instructions?.review || ""}
                  onChange={(e) => updateRoleInstructions("review", e.target.value)}
                  placeholder={"e.g.\nAlways call out missing tests for new code paths.\nFlag any new dependency additions for discussion.\nCheck that error messages are user-facing-safe."}
                  style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                />
              </label>
              <label>
                Investigator instructions
                <textarea
                  rows={4}
                  value={config.agents?.role_instructions?.investigation || ""}
                  onChange={(e) => updateRoleInstructions("investigation", e.target.value)}
                  placeholder={"e.g.\nCross-reference incidents with the on-call runbook.\nAlways propose a rollback path as the first mitigation.\nIf the stack trace crosses services, list each service involved."}
                  style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                />
              </label>
            </div>

            <div className="setup-hint" style={{ marginTop: 16 }}>
              <strong>Legacy:</strong> the field below is the pre-roles global instruction string.
              Still honored — appended to every coding agent's CLAUDE.md alongside the role-specific
              block above. Safe to leave blank if you've moved to the per-role fields.
            </div>
            <div className="integration-fields">
              <label>
                Global coding custom instructions (legacy)
                <textarea
                  rows={3}
                  value={config.agents?.custom_instructions || ""}
                  onChange={(e) => updateField("agents", "custom_instructions", e.target.value)}
                  placeholder="e.g. Always write tests first. Use conventional commits."
                  style={{ fontFamily: "var(--font)", fontSize: 12 }}
                />
              </label>
              <label>
                Branch Prefix
                <input
                  type="text"
                  value={config.agents?.branch_prefix || "maiko"}
                  onChange={(e) => updateField("agents", "branch_prefix", e.target.value)}
                  placeholder="maiko"
                />
                <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                  Auto-generated branches will be: prefix/task-title-slug
                </span>
              </label>
              <label>
                Allowed Tools (pre-approved for Claude Code sessions)
                <div className="repo-list">
                  {(config.brain?.allowed_tools || []).map((tool, i) => (
                    <div key={i} className="repo-list-item">
                      <span>{tool}</span>
                      <button className="btn-ghost" onClick={() => {
                        const updated = (config.brain?.allowed_tools || []).filter((_, j) => j !== i);
                        setConfig((c) => ({ ...c, brain: { ...c?.brain, allowed_tools: updated } }));
                      }} title="Remove"><span style={{ fontSize: 14, color: "var(--urgent)" }}>&times;</span></button>
                    </div>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                  <input
                    type="text"
                    style={{ flex: 1 }}
                    placeholder="Tool name (e.g. Bash, Edit, mcp__github)"
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && e.target.value.trim()) {
                        const val = e.target.value.trim();
                        const tools = config.brain?.allowed_tools || [];
                        if (!tools.includes(val)) setConfig((c) => ({ ...c, brain: { ...c?.brain, allowed_tools: [...tools, val] } }));
                        e.target.value = "";
                      }
                    }}
                  />
                  <span style={{ fontSize: 10, color: "var(--text-muted)", alignSelf: "center" }}>Press Enter to add</span>
                </div>
              </label>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
