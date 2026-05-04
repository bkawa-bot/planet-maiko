import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

/**
 * Repo checks — purely informational. Points users at the
 * .maiko/checks.json pattern and explains what `check_code()` runs.
 * No state writes; no props besides the collapse handle.
 */
export default function RepoChecksSection() {
  const [open, setOpen] = useState(false);

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>Repo checks (check_code)</span>
      </div>
      {open && (
        <div className="collapsible-body">
          <div className="setup-hint">
            Before an agent says they're done, they call <code>check_code()</code>. It runs two verifier layers in one pass:
          </div>
          <ul style={{ fontSize: 12, color: "var(--text-dim)", lineHeight: 1.6, paddingLeft: 20, margin: "8px 0" }}>
            <li>
              <strong>Mechanical checks</strong> — your repo's tests, linter, typechecker. Auto-detected from <code>pyproject.toml</code> / <code>package.json</code> / <code>Cargo.toml</code> / <code>go.mod</code>, or configured by you.
            </li>
            <li>
              <strong>LoRA verifier</strong> — your team's trained code-review model (if an adapter exists for the repo). The rule layer. Learns from approved Learnings in the Knowledge tab.
            </li>
          </ul>
          <div className="setup-hint" style={{ marginTop: 12 }}>
            To override auto-detect or add custom checks, commit a <code>.maiko/checks.json</code> to your repo root. It replaces auto-detect entirely — list everything you want. Team-visible, version-controlled, reviewed in the same PR as the checks themselves.
          </div>
          <pre style={{
            marginTop: 12, padding: 10, fontSize: 11, lineHeight: 1.5,
            background: "var(--bg)", border: "1px solid var(--border)",
            borderRadius: "var(--radius-xs)", overflow: "auto",
            color: "var(--text-dim)", fontFamily: "monospace",
          }}>{`{
  "checks": [
    { "name": "unit tests", "command": "pytest -x --tb=short" },
    { "name": "lint",       "command": "ruff check ." },
    { "name": "typecheck",  "command": "pyright src/" },
    { "name": "no console.log", "command": "! grep -rn console.log src/" },
    { "name": "secrets",    "command": "trufflehog filesystem ." }
  ]
}`}</pre>
          <div className="setup-hint" style={{ marginTop: 8 }}>
            Exit code 0 is a pass; anything else is a failure. Negate greps with <code>!</code> so "found the anti-pattern" means "check failed."
          </div>
        </div>
      )}
    </section>
  );
}
