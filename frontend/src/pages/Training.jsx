import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import InfoButton from "../components/InfoButton";
import {
  GraduationCap, Loader, Sparkles, Link2,
} from "lucide-react";
import "./Training.css";

export default function Training() {
  const [profiles, setProfiles] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [running, setRunning] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [datasets, setDatasets] = useState([]);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [progress, setProgress] = useState(null);
  const [adapters, setAdapters] = useState([]);
  const [assignAgent, setAssignAgent] = useState("");
  const [assignAdapter, setAssignAdapter] = useState("");
  const [coverage, setCoverage] = useState(null);
  const [showUncovered, setShowUncovered] = useState(false);
  const [filterRepo, setFilterRepo] = useState("");

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

  // Refetch coverage whenever the repo filter changes
  useEffect(() => {
    fetchCoverage(filterRepo);
  }, [filterRepo]);

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

        {/* Repo filter */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
          <label style={{ fontSize: 12, color: "var(--text-muted)" }}>Scope:</label>
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
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              Includes rules scoped to <strong>{filterRepo}</strong> + global rules
            </span>
          )}
        </div>

        {/* Coverage stats */}
        {coverage && (
          <div className="training-dataset-stats" style={{ marginBottom: 12 }}>
            <span className="kstat"><Sparkles size={12} /> {coverage.active_count} active rules{filterRepo && " for this scope"}</span>
            <span className="kstat" style={{ color: "var(--green)" }}>
              {coverage.covered_count} in training data
            </span>
            <span className="kstat" style={{ color: coverage.uncovered_count > 0 ? "var(--pink)" : "var(--text-muted)" }}>
              {coverage.uncovered_count} new {coverage.uncovered_count > 0 && "→ ready to generate"}
            </span>
          </div>
        )}

        {/* Uncovered rules expandable list */}
        {coverage && coverage.uncovered_count > 0 && (
          <div style={{ marginBottom: 12 }}>
            <button
              className="btn btn-sm"
              style={{ background: "transparent" }}
              onClick={() => setShowUncovered(!showUncovered)}
            >
              {showUncovered ? "▼" : "▶"} New rules to be generated ({coverage.uncovered_count})
            </button>
            {showUncovered && (
              <div style={{ marginTop: 6, padding: 8, background: "var(--bg-soft)", borderRadius: 4, fontSize: 11 }}>
                {coverage.uncovered.map((r) => (
                  <div key={r.id} style={{ padding: "3px 0", borderBottom: "1px solid var(--border-soft)" }}>
                    <span style={{ color: "var(--text-muted)" }}>[{r.category}]</span> {r.rule}
                    {r.scope_repo && <span style={{ marginLeft: 6, fontSize: 10, color: "var(--text-muted)" }}>({r.scope_repo})</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="btn btn-primary"
            disabled={generating || (coverage && coverage.uncovered_count === 0)}
            onClick={async () => {
              setGenerating(true);
              const count = coverage?.uncovered_count || 0;
              showToast(`Generating training data for ${count} new rules${filterRepo ? ` (${filterRepo})` : ""}...`, "normal");
              try {
                const result = await api.generateFromRules({ repo: filterRepo || undefined });
                if (result.success) {
                  if (result.message) {
                    showToast(result.message, "normal");
                  } else {
                    showToast(`Generated ${result.pairs} pairs from ${result.rules_processed} rules`, "normal");
                  }
                  fetchDatasets();
                  fetchCoverage(filterRepo);
                } else {
                  showToast(result.error || "Generation failed", "high");
                }
              } catch (err) {
                showToast("Generation failed: " + err.message, "high");
              }
              setGenerating(false);
            }}
          >
            {generating ? <><Loader size={12} className="spin" /> Generating...</> : <><Sparkles size={12} /> Generate New Rules ({coverage?.uncovered_count || 0})</>}
          </button>

          <button
            className="btn btn-sm"
            disabled={generating || !coverage || coverage.active_count === 0}
            onClick={async () => {
              if (!confirm(`Regenerate ALL ${coverage?.active_count || 0} rules from scratch${filterRepo ? ` for ${filterRepo}` : ""}? This will make new Opus calls for every rule.`)) return;
              setGenerating(true);
              showToast(`Regenerating all ${coverage?.active_count} rules...`, "normal");
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
            }}
          >
            Regenerate All
          </button>
        </div>

        {coverage && coverage.uncovered_count === 0 && coverage.active_count > 0 && (
          <p style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 8 }}>
            All active rules are already in training data. New rules will appear here as they graduate from PR comments.
          </p>
        )}

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

    </div>
  );
}
