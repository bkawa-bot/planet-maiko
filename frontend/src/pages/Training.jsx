import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import InfoButton from "../components/InfoButton";
import {
  GraduationCap, Loader, Database, Download, Sparkles, Link2,
} from "lucide-react";
import "./Training.css";

export default function Training() {
  const [profiles, setProfiles] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [running, setRunning] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [datasets, setDatasets] = useState([]);
  const [datasetStats, setDatasetStats] = useState(null);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [progress, setProgress] = useState(null);
  const [adapters, setAdapters] = useState([]);
  const [assignAgent, setAssignAgent] = useState("");
  const [assignAdapter, setAssignAdapter] = useState("");
  const [showLegacy, setShowLegacy] = useState(false);

  const fetchDatasets = () => {
    api.getTrainingDatasets().then(setDatasets).catch(() => {});
    api.getTrainingDatasetStats().then(setDatasetStats).catch(() => {});
  };

  useEffect(() => {
    api.getProfiles().then(setProfiles).catch(() => {});
    api.getAdapters().then(setAdapters).catch(() => {});
    fetchDatasets();
  }, []);

  // Poll training progress while running
  useEffect(() => {
    if (!running) { setProgress(null); return; }
    const interval = setInterval(() => {
      api.getTrainingProgress().then((p) => {
        if (p.status === "training") setProgress(p);
        else if (p.status === "done" || p.status === "failed") setProgress(p);
      }).catch(() => {});
    }, 3000);
    return () => clearInterval(interval);
  }, [running]);

  return (
    <div className="training-page">
      <div className="training-header">
        <h2><GraduationCap size={18} /> Training</h2>
        <InfoButton title={<><GraduationCap size={16} /> LoRA Training Workflow</>}>
          <p>Train a local LoRA adapter that reviews code at commit time. The adapter learns from your team's <em>graduated learnings</em> (rules that emerged from PR comments).</p>
          <h4>The flow</h4>
          <ol>
            <li><strong>Backfill</strong> (Knowledge page) — scans PRs, classifies comments into Learnings.</li>
            <li><strong>Generate from Rules</strong> (Step 1 below) — turns each active Learning into balanced training pairs via Claude Opus.</li>
            <li><strong>Train Model</strong> (Step 2 below) — fine-tunes a LoRA adapter on the dataset.</li>
            <li><strong>Assign</strong> (Step 3) — links the adapter to an agent profile.</li>
          </ol>
          <p><em>"Extract from PRs" is the legacy approach — it pulls raw PR diffs as training data. Most users should use "Generate from Rules" instead.</em></p>
        </InfoButton>
      </div>

      {/* Step 1: Generate from Rules */}
      <div className="training-dataset-section">
        <div className="training-dataset-header">
          <Sparkles size={14} /> Step 1 — Generate Training Data from Rules
          <InfoButton title={<><Sparkles size={16} /> Step 1: Generate from Rules</>}>
            <p>Turns your <strong>active Learnings</strong> (graduated rules from PR comments) into balanced training pairs via Claude Opus.</p>
            <h4>How it works</h4>
            <ol>
              <li>Takes each active Learning (e.g. "Always use parameterized queries")</li>
              <li>Pulls real signals — actual code from your team that triggered this rule</li>
              <li>Generates ~25 synthetic violations + ~25 synthetic passes</li>
            </ol>
            <h4>Cost</h4>
            <p>One Opus call per rule. 20 rules ≈ $5-10.</p>
            <h4>If you have no rules</h4>
            <p>Go to the Knowledge page and click "Backfill from PRs" first to generate Learnings.</p>
          </InfoButton>
        </div>

        <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12 }}>
          Generates synthetic violation/pass examples for each active Learning. This is the recommended way to create training data.
        </p>

        <button
          className="btn btn-primary"
          disabled={generating}
          onClick={async () => {
            setGenerating(true);
            showToast("Generating training data from rules... one Opus call per learning", "normal");
            try {
              const result = await api.generateFromRules();
              if (result.success) {
                showToast(`Generated ${result.pairs} pairs from ${result.rules_processed} rules (${result.violations} violations, ${result.passes} passes)`, "normal");
                fetchDatasets();
              } else {
                showToast(result.error || "Generation failed", "high");
              }
            } catch (err) {
              showToast("Generation failed: " + err.message, "high");
            }
            setGenerating(false);
          }}
        >
          {generating ? <><Loader size={12} className="spin" /> Generating...</> : <><Sparkles size={12} /> Generate from Rules</>}
        </button>

        {/* Existing datasets */}
        {datasets.length > 0 && (
          <>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 16, marginBottom: 6 }}>
              Existing datasets
            </div>
            <div className="training-dataset-list">
              {datasets.map((d) => (
                <div key={d.filename} className="training-dataset-item">
                  <span className="training-dataset-name">{d.filename}</span>
                  <span className="training-dataset-count">{d.examples} examples</span>
                  <span className="training-dataset-size">{(d.size_bytes / 1024).toFixed(0)} KB</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Step 2: Train LoRA Model */}
      <div className="training-dataset-section">
        <div className="training-dataset-header">
          <GraduationCap size={14} /> Step 2 — Train LoRA Model
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
          <select
            className="training-select"
            value={selectedAgent}
            onChange={(e) => setSelectedAgent(e.target.value)}
          >
            <option value="">Choose an agent...</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name} — {p.extra?.trained_on_examples ? `v${p.extra?.train_version || 1} (${p.extra.trained_on_examples} examples)` : "untrained"}
              </option>
            ))}
          </select>
          <select
            className="training-select"
            value={selectedDataset}
            onChange={(e) => setSelectedDataset(e.target.value)}
          >
            <option value="">Latest dataset (auto)</option>
            {datasets.map((d) => (
              <option key={d.filename} value={d.path}>
                {d.filename.replace(".jsonl", "")} ({d.examples} examples)
              </option>
            ))}
          </select>
          <button
            className="btn btn-primary"
            disabled={!selectedAgent || !datasets.length || running}
            onClick={async () => {
              setRunning(true);
              showToast("Training LoRA adapter... this may take 20-30 minutes", "normal");
              try {
                const payload = { agent_profile_id: selectedAgent };
                if (selectedDataset) payload.dataset_path = selectedDataset;
                const result = await api.trainAgent(payload);
                if (result.success) {
                  showToast(`Training complete! Adapter saved (${result.examples} examples, ${result.duration_seconds}s)`, "normal");
                  api.getProfiles().then(setProfiles).catch(() => {});
                } else {
                  showToast(result.error || "Training failed", "high");
                  if (result.install_hint) showToast(`Install: ${result.install_hint}`, "normal");
                }
              } catch (err) {
                showToast("Training failed: " + err.message, "high");
              }
              setRunning(false);
            }}
          >
            {running ? <><Loader size={12} className="spin" /> Training...</> : <><GraduationCap size={12} /> Train Model</>}
          </button>
        </div>

        {/* Training progress */}
        {running && progress && progress.status === "training" && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
              <span>Iteration {progress.iteration}/{progress.total_iters} ({progress.percent}%)</span>
              <span>Loss: {progress.loss?.toFixed(3)}</span>
              {progress.tokens_sec && <span>{progress.tokens_sec.toFixed(0)} tok/s</span>}
            </div>
            <div className="training-score-track" style={{ height: 8 }}>
              <div className="training-score-fill" style={{ width: `${progress.percent}%`, background: "var(--pink)", transition: "width 0.5s" }} />
            </div>
          </div>
        )}

        {!datasets.length && (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Extract training data from PRs first (above), then train a model.
          </div>
        )}

        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>
          Requires: <code>pip install mlx mlx-lm</code> (Mac) or <code>pip install torch unsloth</code> (NVIDIA)
        </div>
      </div>

      {/* Step 3: Assign Existing Adapter */}
      {adapters.length > 0 && (
        <div className="training-dataset-section">
          <div className="training-dataset-header">
            <Link2 size={14} /> Step 3 — Assign Adapter to Agent
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select
              className="training-select"
              value={assignAgent}
              onChange={(e) => setAssignAgent(e.target.value)}
            >
              <option value="">Choose an agent...</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name} {p.extra?.adapter_path ? `(current: ${p.extra.adapter_path.split("/").pop()})` : "(no adapter)"}
                </option>
              ))}
            </select>
            <select
              className="training-select"
              value={assignAdapter}
              onChange={(e) => setAssignAdapter(e.target.value)}
            >
              <option value="">Choose an adapter...</option>
              {adapters.filter((a) => a.has_weights).map((a) => (
                <option key={a.name} value={a.path}>{a.name}</option>
              ))}
            </select>
            <button
              className="btn btn-primary"
              disabled={!assignAgent || !assignAdapter}
              onClick={async () => {
                try {
                  await api.assignAdapter({ agent_profile_id: assignAgent, adapter_path: assignAdapter });
                  showToast("Adapter assigned to agent", "normal");
                  api.getProfiles().then(setProfiles).catch(() => {});
                } catch (err) {
                  showToast("Failed: " + err.message, "high");
                }
              }}
            >
              <Link2 size={12} /> Assign
            </button>
          </div>

          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>
            Link an existing trained adapter to an agent profile. The agent's pre-commit review will use this adapter.
          </div>
        </div>
      )}

      {/* Legacy: Extract from PRs (raw, noisy) */}
      <div className="training-dataset-section" style={{ opacity: 0.7 }}>
        <div
          className="training-dataset-header"
          style={{ cursor: "pointer" }}
          onClick={() => setShowLegacy(!showLegacy)}
        >
          <Database size={14} /> Legacy: Extract from PRs
          <span style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: 8 }}>
            (raw approach — most users should use Step 1 instead)
          </span>
        </div>

        {showLegacy && (
          <>
            <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8, marginBottom: 12 }}>
              Scrapes raw PR review comments and pairs them with the diffs as training data. Output is noisy because every reviewer comment becomes a "VIOLATION" example, even casual ones. Kept here for completeness.
            </p>

            <div className="training-dataset-stats">
              {datasetStats && datasetStats.total > 0 ? (
                <>
                  <span className="kstat"><Database size={12} /> {datasetStats.total} examples</span>
                  <span className="kstat" style={{ color: "var(--urgent)" }}>{datasetStats.violations} violations</span>
                  <span className="kstat" style={{ color: "var(--green)" }}>{datasetStats.passes} passes</span>
                </>
              ) : (
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>No raw extracted data yet</span>
              )}
            </div>

            <button
              className="btn btn-sm"
              disabled={extracting}
              onClick={async () => {
                setExtracting(true);
                showToast("Extracting raw training data from PR history...", "normal");
                try {
                  const result = await api.exportTrainingDataset();
                  showToast(`Extracted ${result.pairs} pairs (${result.violations} violations, ${result.passes} passes) from ${result.repos_scanned} repos`, "normal");
                  fetchDatasets();
                } catch (err) {
                  showToast("Extraction failed: " + err.message, "high");
                }
                setExtracting(false);
              }}
            >
              {extracting ? <><Loader size={12} className="spin" /> Extracting...</> : <><Download size={12} /> Extract Raw Data</>}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
