import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import InfoButton from "../components/InfoButton";
import ConfirmModal from "../components/ConfirmModal";
import {
  GraduationCap, Loader, Sparkles, Link2, ChevronDown, ChevronRight,
} from "lucide-react";
import "./Training.css";

const PROGRESS_POLL_MS = 3000;

export default function Training() {
  const [profiles, setProfiles] = useState([]);
  const [running, setRunning] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatingProgress, setGeneratingProgress] = useState("");
  const [datasets, setDatasets] = useState([]);
  const [showDatasets, setShowDatasets] = useState(false);
  const [progress, setProgress] = useState(null);
  const [adapters, setAdapters] = useState([]);
  const [assignAgent, setAssignAgent] = useState("");
  const [assignAdapter, setAssignAdapter] = useState("");
  const [coverage, setCoverage] = useState(null);
  const [filterRepo, setFilterRepo] = useState("");
  const [confirmTraining, setConfirmTraining] = useState(false);
  const [confirmRegenAll, setConfirmRegenAll] = useState(false);

  const fetchDatasets = () => {
    api.getTrainingDatasets().then(setDatasets).catch(() => {});
  };

  const fetchCoverage = (repo) => {
    api.getRuleCoverage(repo).then(setCoverage).catch(() => {});
  };

  useEffect(() => {
    api.getProfiles().then(setProfiles).catch(() => {});
    api.getAdapters().then(setAdapters).catch(() => {});
    fetchDatasets();
  }, []);

  useEffect(() => { fetchCoverage(filterRepo); }, [filterRepo]);

  // Poll training progress while running
  useEffect(() => {
    if (!running) { setProgress(null); return; }
    const interval = setInterval(() => {
      api.getTrainingProgress().then((p) => {
        if (p.status === "training") setProgress(p);
        else if (p.status === "done" || p.status === "failed") setProgress(p);
      }).catch(() => {});
    }, PROGRESS_POLL_MS);
    return () => clearInterval(interval);
  }, [running]);

  const totalDatasetExamples = datasets.reduce((sum, d) => sum + d.examples, 0);

  const startTraining = async () => {
    setRunning(true);
    setConfirmTraining(false);
    showToast("Training LoRA adapter... this may take 20-30 minutes", "normal");
    try {
      const result = await api.trainAgent({});
      if (result.success) {
        showToast(`Training complete! Adapter saved (${result.examples} examples, ${result.duration_seconds}s)`, "normal");
        api.getAdapters().then(setAdapters).catch(() => {});
        api.getProfiles().then(setProfiles).catch(() => {});
      } else {
        showToast(result.error || "Training failed", "high");
        if (result.install_hint) showToast(`Install: ${result.install_hint}`, "normal");
      }
    } catch (err) {
      showToast("Training failed: " + err.message, "high");
    }
    setRunning(false);
  };

  const startRegenerateAll = async () => {
    setConfirmRegenAll(false);
    setGenerating(true);
    setGeneratingProgress(`Regenerating all ${coverage?.active_count} rules...`);
    try {
      const result = await api.generateFromRules({ force: true, repo: filterRepo || undefined });
      if (result.success) {
        showToast(`Generated ${result.pairs} pairs from ${result.rules_processed} rules`, "normal");
        fetchDatasets();
        fetchCoverage(filterRepo);
      } else {
        showToast(result.error || "Generation failed", "high");
      }
    } catch (err) {
      showToast("Generation failed: " + err.message, "high");
    }
    setGenerating(false);
    setGeneratingProgress("");
  };

  return (
    <div className="training-page">
      <div className="training-header">
        <h2><GraduationCap size={18} /> Training</h2>
        <InfoButton title={<><GraduationCap size={16} /> LoRA Training Workflow</>}>
          <p>Train a local LoRA adapter that reviews code at commit time. The adapter learns from your team's <em>graduated learnings</em> (rules that emerged from PR comments).</p>
          <h4>The flow</h4>
          <ol>
            <li><strong>Generate from Rules</strong> (Step 1) — turns each active Learning into balanced training pairs via Claude Opus.</li>
            <li><strong>Train Model</strong> (Step 2) — fine-tunes a LoRA adapter on the dataset.</li>
            <li><strong>Assign</strong> (Step 3) — links the adapter to an agent profile.</li>
          </ol>
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
            <p>One Opus call per rule. 20 rules ~ $5-10.</p>
          </InfoButton>
        </div>

        {/* Repo filter */}
        <div className="training-row">
          <label className="training-label">Scope:</label>
          <select
            className="training-select"
            value={filterRepo}
            onChange={(e) => setFilterRepo(e.target.value)}
          >
            <option value="">All repos (global dataset)</option>
            {coverage?.available_repos?.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
          {filterRepo && (
            <span className="training-hint">
              Includes rules scoped to <strong>{filterRepo}</strong> + global rules
            </span>
          )}
        </div>

        {/* Coverage stats */}
        {coverage && (
          <div className="training-dataset-stats">
            <span className="kstat"><Sparkles size={12} /> {coverage.active_count} active rules{filterRepo && " for this scope"}</span>
            <span className="kstat" style={{ color: "var(--green)" }}>
              {coverage.covered_count} in training data
            </span>
            {coverage.uncovered_count > 0 && (
              <span className="kstat" style={{ color: "var(--pink)" }}>
                {coverage.uncovered_count} new
              </span>
            )}
          </div>
        )}

        <div className="training-row">
          <button
            className="btn btn-primary"
            disabled={generating || (coverage && coverage.uncovered_count === 0)}
            onClick={async () => {
              setGenerating(true);
              const count = coverage?.uncovered_count || 0;
              setGeneratingProgress(`Generating training data for ${count} rule(s)...`);
              try {
                const result = await api.generateFromRules({ repo: filterRepo || undefined });
                if (result.success) {
                  showToast(result.message || `Generated ${result.pairs} pairs from ${result.rules_processed} rules`, "normal");
                  fetchDatasets();
                  fetchCoverage(filterRepo);
                } else {
                  showToast(result.error || "Generation failed", "high");
                }
              } catch (err) {
                showToast("Generation failed: " + err.message, "high");
              }
              setGenerating(false);
              setGeneratingProgress("");
            }}
          >
            {generating ? <><Loader size={12} className="spin" /> Generating...</> : <><Sparkles size={12} /> Generate New Rules ({coverage?.uncovered_count || 0})</>}
          </button>

          <button
            className="btn btn-sm"
            disabled={generating || !coverage || coverage.active_count === 0}
            onClick={() => setConfirmRegenAll(true)}
          >
            Regenerate All
          </button>
        </div>

        {/* Generation progress indicator */}
        {generating && generatingProgress && (
          <div className="training-progress-bar">
            <Loader size={12} className="spin" />
            <span>{generatingProgress}</span>
            <span className="training-hint">This calls Opus per rule and may take a few minutes.</span>
          </div>
        )}

        {coverage && coverage.uncovered_count === 0 && coverage.active_count > 0 && (
          <p className="training-hint" style={{ marginTop: 8 }}>
            All active rules are covered. New rules will appear here as they graduate from PR comments.
          </p>
        )}

        {/* Existing datasets — collapsible */}
        {datasets.length > 0 && (
          <div className="training-datasets-collapse">
            <button className="btn-collapse" onClick={() => setShowDatasets(!showDatasets)}>
              {showDatasets ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              {datasets.length} dataset(s) ({totalDatasetExamples} total examples)
            </button>
            {showDatasets && (
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
        )}
      </div>

      {/* Step 2: Train LoRA Model (no agent selection needed) */}
      <div className="training-dataset-section">
        <div className="training-dataset-header">
          <GraduationCap size={14} /> Step 2 — Train LoRA Model
        </div>

        <div className="training-row">
          <button
            className="btn btn-primary"
            disabled={!datasets.length || running}
            onClick={() => setConfirmTraining(true)}
          >
            {running ? <><Loader size={12} className="spin" /> Training...</> : <><GraduationCap size={12} /> Train Model</>}
          </button>
        </div>

        {/* Training progress */}
        {running && progress && progress.status === "training" && (
          <div className="training-live-progress">
            <div className="training-progress-stats">
              <span>Iteration {progress.iteration}/{progress.total_iters} ({progress.percent}%)</span>
              <span>Loss: {progress.loss?.toFixed(3)}</span>
              {progress.tokens_sec && <span>{progress.tokens_sec.toFixed(0)} tok/s</span>}
            </div>
            <div className="training-score-track" style={{ height: 8 }}>
              <div className="training-score-fill" style={{ width: `${progress.percent}%`, background: "var(--pink)", transition: "width 0.5s" }} />
            </div>
          </div>
        )}

        {running && !progress && (
          <div className="training-progress-bar">
            <Loader size={12} className="spin" />
            <span>Preparing training data and starting model...</span>
          </div>
        )}

        {!datasets.length && (
          <p className="training-hint">Generate training data in Step 1 first.</p>
        )}

        <p className="training-hint" style={{ marginTop: 8 }}>
          Requires: <code>pip install mlx mlx-lm</code> (Mac) or <code>pip install torch unsloth</code> (NVIDIA)
        </p>
      </div>

      {/* Step 3: Assign Adapter to Agent */}
      {adapters.length > 0 && (
        <div className="training-dataset-section">
          <div className="training-dataset-header">
            <Link2 size={14} /> Step 3 — Assign Adapter to Agent
          </div>

          <div className="training-row">
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

          <p className="training-hint" style={{ marginTop: 8 }}>
            Link a trained adapter to an agent profile. The agent's pre-commit review will use this adapter.
          </p>
        </div>
      )}

      <ConfirmModal
        open={confirmTraining}
        title="Training is resource-intensive"
        body={<>
          <p>
            This fine-tunes a LoRA adapter on the combined dataset
            ({totalDatasetExamples.toLocaleString()} examples). Runs locally — expect heavy GPU/CPU use for <strong>20-30 minutes</strong> and no interruption.
          </p>
          <p>The server stays responsive, but your machine will get warm.</p>
          <span className="confirm-estimate">~20-30 min · local GPU/CPU · no API cost</span>
        </>}
        confirmText="Train model"
        onCancel={() => setConfirmTraining(false)}
        onConfirm={startTraining}
      />

      <ConfirmModal
        open={confirmRegenAll}
        severity="danger"
        title={`Regenerate ALL ${coverage?.active_count || 0} rules?`}
        body={<>
          <p>
            This will make a <strong>new Opus call per rule</strong> — overwriting the existing training pairs. Expensive and slow.
          </p>
          <p>Only do this if rules have changed materially or the existing dataset feels stale.</p>
          <span className="confirm-estimate">
            ~{coverage?.active_count || 0} Opus calls · ≈ $0.25–$0.50 per rule
          </span>
        </>}
        confirmText="Regenerate all"
        onCancel={() => setConfirmRegenAll(false)}
        onConfirm={startRegenerateAll}
      />
    </div>
  );
}
