import { useState } from "react";
import { Download, Upload } from "@icons";
import { api } from "../../api/client";
import "../../pages/Settings.css"; // carries its own card styling wherever it's mounted

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
    <div className="team-rules-actions" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <button className="btn btn-sm" onClick={handleExport} disabled={exporting} title="Export your active rules to a JSON file a teammate can import">
        <Download size={10} /> {exporting ? "Exporting…" : "Export rules"}
      </button>
      <label
        className="btn btn-sm"
        style={{ cursor: importing ? "default" : "pointer", opacity: importing ? 0.7 : 1 }}
        title="Import a JSON file exported from another machine"
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
      {result && (
        <small style={{ color: result.kind === "error" ? "var(--urgent)" : "var(--text-muted)" }}>
          {result.kind === "export" && (
            <>Exported {result.count} rule{result.count === 1 ? "" : "s"}.</>
          )}
          {result.kind === "import" && (
            <>
              Imported {result.imported}, skipped {result.skipped_duplicate} dup{result.skipped_duplicate === 1 ? "" : "s"}
              {result.errors?.length ? `, ${result.errors.length} error${result.errors.length === 1 ? "" : "s"}` : ""}
              {result.imported > 0 && " (embeddings rebuilding)"}
            </>
          )}
          {result.kind === "error" && result.message}
        </small>
      )}
    </div>
  );
}
