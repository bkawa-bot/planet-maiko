import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import InfoButton from "../components/InfoButton";
import {
  GraduationCap, Loader, Database, Download, Sparkles,
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

  const fetchDatasets = () => {
    api.getTrainingDatasets().then(setDatasets).catch(() => {});
    api.getTrainingDatasetStats().then(setDatasetStats).catch(() => {});
  };

  useEffect(() => {
    api.getProfiles().then(setProfiles).catch(() => {});
    fetchDatasets();
  }, []);

  return (
    <div className="training-page">
      <div className="training-header">
        <h2><GraduationCap size={18} /> Training</h2>
        <InfoButton title={<><GraduationCap size={16} /> LoRA Training</>}>
          <p>Train small LoRA adapters on your team's real PR review history. Each adapter encodes coding patterns so agents can run compliance checks locally — free and fast.</p>
          <h4>How it works</h4>
          <ol>
            <li><strong>Extract training data</strong> — scans merged PRs for review comments (violations) and clean merges (passes).</li>
            <li><strong>Pick a dataset</strong> — per-repo for specialized agents, or combined for generalists.</li>
            <li><strong>Train an agent</strong> — LoRA fine-tuning runs locally via MLX (Apple Silicon) or PyTorch (NVIDIA).</li>
            <li><strong>Adapter saved</strong> — linked to the agent's profile for future use.</li>
          </ol>
        </InfoButton>
      </div>

      {/* Training Data Extraction */}
      <div className="training-dataset-section">
        <div className="training-dataset-header">
          <Database size={14} /> Training Data
          <InfoButton title={<><Database size={16} /> Training Data</>}>
            <p>Extract real code + review comment pairs from your PR history as training data for LoRA fine-tuning.</p>
            <h4>What it does</h4>
            <p>Scans merged PRs in your configured repos. For each PR with review comments, it pairs the code that was reviewed with the reviewer's feedback. Clean merges become "PASS" examples.</p>
            <h4>How much data do you need?</h4>
            <p>300-500 pairs per repo is a good baseline. 1,000+ is the sweet spot. The extraction creates per-repo datasets so you can train repo-specific adapters.</p>
          </InfoButton>
        </div>

        <div className="training-dataset-stats">
          {datasetStats && datasetStats.total > 0 ? (
            <>
              <span className="kstat"><Database size={12} /> {datasetStats.total} examples</span>
              <span className="kstat" style={{ color: "var(--urgent)" }}>{datasetStats.violations} violations</span>
              <span className="kstat" style={{ color: "var(--green)" }}>{datasetStats.passes} passes</span>
              {datasetStats.filename && <span className="kstat">{datasetStats.filename}</span>}
            </>
          ) : (
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>No training data extracted yet</span>
          )}
        </div>

        <button
          className="btn btn-primary"
          disabled={extracting}
          onClick={async () => {
            setExtracting(true);
            showToast("Extracting training data from PR history...", "normal");
            try {
              const result = await api.exportTrainingDataset();
              showToast(`Extracted ${result.pairs} training pairs (${result.violations} violations, ${result.passes} passes) from ${result.repos_scanned} repos`, "normal");
              fetchDatasets();
            } catch (err) {
              showToast("Extraction failed: " + err.message, "high");
            }
            setExtracting(false);
          }}
        >
          {extracting ? <><Loader size={12} className="spin" /> Extracting...</> : <><Download size={12} /> Extract from PRs</>}
        </button>

        {datasets.length > 0 && (
          <div className="training-dataset-list">
            {datasets.map((d) => (
              <div key={d.filename} className="training-dataset-item">
                <span className="training-dataset-name">{d.filename}</span>
                <span className="training-dataset-count">{d.examples} examples</span>
                <span className="training-dataset-size">{(d.size_bytes / 1024).toFixed(0)} KB</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Rule-Based Training Data */}
      <div className="training-dataset-section">
        <div className="training-dataset-header">
          <Sparkles size={14} /> Generate from Rules
          <InfoButton title={<><Sparkles size={16} /> Rule-Based Training Data</>}>
            <p>Generates focused training data from your active learnings. For each rule, it combines real feedback signals with synthetic examples generated by Claude Opus.</p>
            <h4>How it works</h4>
            <ol>
              <li>Takes each active learning (e.g. "Always use parameterized queries")</li>
              <li>Pulls real signals — actual code from your team that triggered this rule</li>
              <li>Generates ~25 synthetic violation examples (realistic code that breaks the rule)</li>
              <li>Generates ~25 synthetic pass examples (clean code that follows the rule)</li>
            </ol>
            <h4>Why this is better</h4>
            <p>Every training example is tied to a specific rule your team cares about. Balanced pass/fail ratio per rule. ~50 examples per rule saturates the pattern. 20 rules = 1,000 focused pairs.</p>
            <h4>Cost</h4>
            <p>One Opus call per rule. 20 rules ~ $5-10.</p>
          </InfoButton>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
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
        </div>
      </div>

      {/* LoRA Model Training */}
      <div className="training-dataset-section">
        <div className="training-dataset-header">
          <GraduationCap size={14} /> Train LoRA Model
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

        {!datasets.length && (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Extract training data from PRs first (above), then train a model.
          </div>
        )}

        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>
          Requires: <code>pip install mlx mlx-lm</code> (Mac) or <code>pip install torch unsloth</code> (NVIDIA)
        </div>
      </div>
    </div>
  );
}
