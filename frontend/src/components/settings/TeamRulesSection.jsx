import { useState } from "react";
import { Download, Upload } from "@icons";
import { api } from "../../api/client";

/**
 * Team rules sharing — export your active rules to a JSON file so a
 * teammate can import them without re-mining months of signals. The
 * export carries rule text + violation_description; the importer
 * regenerates embeddings locally (cheap, sidesteps embedding-model
 * version drift between machines).
 */
export default function TeamRulesSection() {
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    setResult(null);
    try {
      const data = await api.exportRules();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `maiko-rules-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setResult({ kind: "export", count: data.rule_count });
    } catch (e) {
      setResult({ kind: "error", message: e.message || "Export failed" });
    } finally {
      setExporting(false);
    }
  };

  const handleImport = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setImporting(true);
    setResult(null);
    try {
      const text = await file.text();
      const payload = JSON.parse(text);
      const res = await api.importRules(payload);
      setResult({ kind: "import", ...res });
    } catch (e) {
      setResult({ kind: "error", message: e.message || "Import failed" });
    } finally {
      setImporting(false);
    }
  };

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" style={{ cursor: "default" }}>
        <span>Team rules sharing</span>
      </div>
      <div className="collapsible-body">
        <div className="integration-section">
          <div className="setup-hint">
            Export your team's active rules so a teammate can import them
            without re-mining months of signals. The export includes the
            rule text, category, scope, and the Claude-generated violation
            description. Embeddings are regenerated locally after import —
            cheap, and avoids version drift between machines.
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
            <button className="btn btn-sm" onClick={handleExport} disabled={exporting}>
              <Download size={10} /> {exporting ? "Exporting…" : "Export rules"}
            </button>
            <label
              className="btn btn-sm"
              style={{ cursor: importing ? "default" : "pointer", opacity: importing ? 0.7 : 1 }}
            >
              <Upload size={10} /> {importing ? "Importing…" : "Import rules"}
              <input
                type="file"
                accept="application/json,.json"
                onChange={handleImport}
                disabled={importing}
                style={{ display: "none" }}
              />
            </label>
          </div>

          {result && (
            <div className="setup-hint" style={{ marginTop: 12 }}>
              {result.kind === "export" && (
                <>Exported {result.count} rule{result.count === 1 ? "" : "s"} — file downloaded.</>
              )}
              {result.kind === "import" && (
                <>
                  Imported {result.imported} rule{result.imported === 1 ? "" : "s"} ·{" "}
                  skipped {result.skipped_duplicate} duplicate{result.skipped_duplicate === 1 ? "" : "s"}
                  {result.errors?.length ? ` · ${result.errors.length} error${result.errors.length === 1 ? "" : "s"}` : ""}
                  {result.imported > 0 && (
                    <> · embeddings regenerating in the background.</>
                  )}
                </>
              )}
              {result.kind === "error" && (
                <span style={{ color: "var(--urgent)" }}>{result.message}</span>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
