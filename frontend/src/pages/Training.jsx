import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import InfoButton from "../components/InfoButton";
import ConfirmModal from "../components/ConfirmModal";
import {
  GraduationCap, Loader, Sparkles, ChevronDown, ChevronRight,
} from "lucide-react";
import "./Training.css";

const PROGRESS_POLL_MS = 3000;

// Two-line sparkline showing train + val loss over iters. Hand-rolled
// SVG (no chart lib) because the shape is trivial and we want to keep
// the bundle lean. The val curve diverging upward from the train curve
// is the textbook overfit signal.
function LossSparkline({ history, totalIters }) {
  if (!history || history.length < 2) return null;

  const W = 280;
  const H = 64;
  const PAD = 4;

  // Auto-scale Y to the highest observed loss; clamp the floor so a
  // perfectly converged run still has visible amplitude.
  const allLosses = history.flatMap((p) => [p.train, p.val].filter((v) => v != null));
  if (!allLosses.length) return null;
  const yMax = Math.max(...allLosses, 0.1) * 1.05;
  const yMin = 0;

  const xMax = totalIters || history[history.length - 1].iter || 1;
  const xOf = (it) => PAD + (it / xMax) * (W - 2 * PAD);
  const yOf = (loss) => H - PAD - ((loss - yMin) / (yMax - yMin)) * (H - 2 * PAD);

  const buildPath = (key) => {
    let d = "";
    let started = false;
    for (const point of history) {
      const v = point[key];
      if (v == null) continue;
      const x = xOf(point.iter);
      const y = yOf(v);
      d += `${started ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)} `;
      started = true;
    }
    return d.trim();
  };

  const trainPath = buildPath("train");
  const valPath = buildPath("val");

  return (
    <svg
      className="loss-sparkline"
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Train and validation loss over training iterations"
    >
      {/* Y-axis baseline at 0 for visual anchor */}
      <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} className="loss-sparkline-axis" />
      {trainPath && <path d={trainPath} className="loss-sparkline-line loss-sparkline-train" />}
      {valPath && <path d={valPath} className="loss-sparkline-line loss-sparkline-val" />}
    </svg>
  );
}

// Hint surfaced beneath the sparkline. Keeps the read short — the
// chart is the canonical signal; this just calls out a number when
// it crosses an obvious threshold.
function OverfitHint({ trainLoss, valLoss }) {
  if (trainLoss == null || valLoss == null) return null;
  const gap = valLoss - trainLoss;
  let label, severity;
  if (gap < 0.1) { label = "healthy"; severity = "ok"; }
  else if (gap < 0.3) { label = "watching"; severity = "warn"; }
  else { label = "overfit suspected"; severity = "high"; }
  return (
    <div className={`overfit-hint overfit-hint-${severity}`}>
      val − train = {gap.toFixed(2)} · {label}
    </div>
  );
}

export default function Training() {
  const [running, setRunning] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatingProgress, setGeneratingProgress] = useState("");
  const [ruleGenState, setRuleGenState] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [showDatasets, setShowDatasets] = useState(false);
  const [selectedDataset, setSelectedDataset] = useState("");
  const [progress, setProgress] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [filterRepo, setFilterRepo] = useState("");
  const [confirmTraining, setConfirmTraining] = useState(false);
  const [confirmRegenAll, setConfirmRegenAll] = useState(false);
  // Advanced training knobs — collapsed by default. Defaults match
  // the backend DEFAULT_TRAINING_CONFIG so an unopened section
  // produces the same behavior as not sending anything.
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [advEpochs, setAdvEpochs] = useState(2);
  const [advMaxSeqLength, setAdvMaxSeqLength] = useState(1024);
  const [advGradCheckpoint, setAdvGradCheckpoint] = useState(false);
  const [advEarlyStopPatience, setAdvEarlyStopPatience] = useState(3);
  const [advResumeFrom, setAdvResumeFrom] = useState("");
  const [adapters, setAdapters] = useState([]);

  const fetchDatasets = () => {
    api.getTrainingDatasets().then(setDatasets).catch(() => {});
  };
  const fetchAdapters = () => {
    api.getAdapters().then(setAdapters).catch(() => {});
  };

  const fetchCoverage = (repo) => {
    api.getRuleCoverage(repo).then(setCoverage).catch(() => {});
  };

  useEffect(() => {
    fetchDatasets();
    fetchAdapters();
    // Resume progress displays if a job is already running when the
    // page mounts (user navigated away and came back, or refreshed).
    // Without this, `running`/`generating` default to false and the
    // polling effects never kick in, so a live job is invisible.
    api.getTrainingProgress()
      .then((p) => {
        if (p && p.status && p.status !== "done" && p.status !== "failed" && p.status !== "idle") {
          setProgress(p);
          setRunning(true);
        }
      })
      .catch(() => {});
    api.getRuleGenProgress()
      .then((s) => {
        if (s && s.status && s.status !== "done" && s.status !== "failed" && s.status !== "idle") {
          setRuleGenState(s);
          setGenerating(true);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => { fetchCoverage(filterRepo); }, [filterRepo]);

  // Poll training progress while running. The POST now returns 202
  // immediately, so completion (done/failed) is detected here via the
  // adapter's progress.json instead of from the POST response.
  useEffect(() => {
    if (!running) { setProgress(null); return; }
    const tick = async () => {
      try {
        const p = await api.getTrainingProgress();
        setProgress(p);
        if (p.status === "done") {
          showToast(
            `Training complete! Adapter saved${p.adapter_name ? ` (${p.adapter_name})` : ""}`,
            "normal",
          );
          setRunning(false);
        } else if (p.status === "failed") {
          showToast(p.error || "Training failed", "high");
          if (p.install_hint) showToast(`Install: ${p.install_hint}`, "normal");
          setRunning(false);
        }
      } catch { /* transient — keep polling */ }
    };
    tick();
    const interval = setInterval(tick, PROGRESS_POLL_MS);
    return () => clearInterval(interval);
  }, [running]);

  // Poll rule-generation progress while a job is running. The backend
  // now runs the Opus calls in a thread, so the click returns in under
  // a second with status="started"; the UI tracks progress here
  // instead of waiting on the HTTP request (which used to time out
  // and leave the spinner stuck).
  useEffect(() => {
    if (!generating) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await api.getRuleGenProgress();
        if (cancelled) return;
        setRuleGenState(s);
        if (s.status === "done" || s.status === "failed") {
          setGenerating(false);
          if (s.status === "done") {
            showToast(s.message || `Generated ${s.pairs} pairs from ${s.rules_processed} rules`, "normal");
            fetchDatasets();
            fetchCoverage(filterRepo);
          } else {
            showToast(s.message || "Rule generation failed", "high");
          }
        } else if (s.total_rules > 0) {
          setGeneratingProgress(
            `Rule ${s.rules_processed + 1} of ${s.total_rules}${s.current_rule ? ": " + s.current_rule : ""}`
          );
        }
      } catch { /* poll errors are recoverable */ }
    };
    tick();
    const interval = setInterval(tick, PROGRESS_POLL_MS);
    return () => { cancelled = true; clearInterval(interval); };
  }, [generating, filterRepo]);

  const totalDatasetExamples = datasets.reduce((sum, d) => sum + d.examples, 0);

  const startTraining = async () => {
    setRunning(true);
    setConfirmTraining(false);
    showToast("Training LoRA adapter — this runs in the background (~20-30 min).", "normal");
    try {
      const result = await api.trainAgent({
        repo: filterRepo || undefined,
        dataset_path: selectedDataset || undefined,
        config: {
          epochs: advEpochs,
          max_seq_length: advMaxSeqLength,
          grad_checkpoint: advGradCheckpoint,
          early_stop_patience: advEarlyStopPatience,
          ...(advResumeFrom ? { resume_adapter_file: advResumeFrom } : {}),
        },
      });
      if (result?.status === "started") {
        // Async path — the polling effect above tracks completion.
        // Nothing else to do here; running stays true until progress
        // reports done/failed.
      } else if (result?.success) {
        // Defensive: if the endpoint reverts to sync later, handle it.
        showToast(`Training complete! Adapter saved`, "normal");
        setRunning(false);
      } else {
        showToast(result?.error || "Training failed to start", "high");
        if (result?.install_hint) showToast(`Install: ${result.install_hint}`, "normal");
        setRunning(false);
      }
    } catch (err) {
      showToast("Training failed: " + err.message, "high");
      setRunning(false);
    }
  };

  const startRegenerateAll = async () => {
    setConfirmRegenAll(false);
    setGenerating(true);
    setGeneratingProgress(`Queuing ${coverage?.active_count} rules…`);
    try {
      const result = await api.generateFromRules({ force: true, repo: filterRepo || undefined });
      if (result?.status === "started") {
        // Async — the polling effect above takes over from here.
        showToast("Rule generation running in the background.", "normal");
      } else if (result?.success) {
        // Shouldn't hit on force=true, but handle graceful finish.
        showToast(result.message || `Generated ${result.pairs} pairs from ${result.rules_processed} rules`, "normal");
        fetchDatasets();
        fetchCoverage(filterRepo);
        setGenerating(false);
      } else {
        showToast(result?.error || "Generation failed", "high");
        setGenerating(false);
      }
    } catch (err) {
      showToast("Generation failed: " + err.message, "high");
      setGenerating(false);
    }
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
            <li><strong>Train Model</strong> (Step 2) — fine-tunes a LoRA adapter scoped to a repo (or "global" by default).</li>
          </ol>
          <p>Trained adapters are picked up automatically by inference — name-based discovery resolves <code>lora-&lt;repo&gt;-…</code> first, then falls back to <code>lora-global-…</code>.</p>
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
              setGeneratingProgress(`Queuing ${count} rule(s)…`);
              try {
                const result = await api.generateFromRules({ repo: filterRepo || undefined });
                if (result?.status === "started") {
                  // Async — polling effect handles completion.
                  showToast("Rule generation running in the background.", "normal");
                } else if (result?.success) {
                  // Incremental path returned synchronously (nothing to do)
                  showToast(result.message || `Generated ${result.pairs} pairs from ${result.rules_processed} rules`, "normal");
                  fetchDatasets();
                  fetchCoverage(filterRepo);
                  setGenerating(false);
                } else {
                  showToast(result?.error || "Generation failed", "high");
                  setGenerating(false);
                }
              } catch (err) {
                showToast("Generation failed: " + err.message, "high");
                setGenerating(false);
              }
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

        {/* Generation progress indicator — drives off the async
            rule-gen state once it starts reporting, falls back to
            the initial queuing message before the first poll lands. */}
        {generating && (
          <div className="training-progress-bar">
            <Loader size={12} className="spin" />
            <span>
              {ruleGenState?.total_rules > 0
                ? `${ruleGenState.rules_processed}/${ruleGenState.total_rules} rules processed${ruleGenState.pairs ? ` · ${ruleGenState.pairs} pairs` : ""}`
                : generatingProgress || "Starting…"}
            </span>
            {ruleGenState?.current_rule && (
              <span className="training-hint" title={ruleGenState.current_rule}>
                {ruleGenState.current_rule.slice(0, 80)}…
              </span>
            )}
            {!ruleGenState?.current_rule && (
              <span className="training-hint">Opus per rule — runs in the background; safe to navigate away.</span>
            )}
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

      {/* Step 2: Train LoRA Model (scoped to repo, or "global" by default) */}
      <div className="training-dataset-section">
        <div className="training-dataset-header">
          <GraduationCap size={14} /> Step 2 — Train LoRA Model
        </div>

        <div className="training-row">
          <label className="training-label">Train LoRA for:</label>
          <select
            className="training-select"
            value={filterRepo}
            onChange={(e) => setFilterRepo(e.target.value)}
          >
            <option value="">Global (default fallback)</option>
            {coverage?.available_repos?.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>

        <div className="training-row">
          <label className="training-label">Dataset:</label>
          <select
            className="training-select"
            value={selectedDataset}
            onChange={(e) => setSelectedDataset(e.target.value)}
            disabled={!datasets.length}
          >
            <option value="">Auto-pick (latest matching scope)</option>
            {datasets.map((d) => (
              <option key={d.path} value={d.path}>
                {d.filename} — {d.examples} examples
              </option>
            ))}
          </select>
        </div>

        <div className="training-row">
          <button
            className="btn-collapse"
            onClick={() => setShowAdvanced((v) => !v)}
            type="button"
          >
            {showAdvanced ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Advanced
          </button>
        </div>

        {showAdvanced && (
          <div className="training-advanced">
            <div className="training-row">
              <label className="training-label">Epochs:</label>
              <input
                className="training-input training-input-num"
                type="number"
                min="1"
                max="10"
                value={advEpochs}
                onChange={(e) => setAdvEpochs(Math.max(1, Number(e.target.value) || 1))}
              />
              <span className="training-hint">Default 2. Higher = more passes; risk of overfit on synthetic data past 3.</span>
            </div>

            <div className="training-row">
              <label className="training-label">Max seq length:</label>
              <select
                className="training-select"
                value={advMaxSeqLength}
                onChange={(e) => setAdvMaxSeqLength(Number(e.target.value))}
              >
                <option value={256}>256 (lightest, may truncate)</option>
                <option value={512}>512 (recommended for OOM-prone runs)</option>
                <option value={1024}>1024 (default)</option>
                <option value={2048}>2048 (long pairs only, heavy memory)</option>
              </select>
            </div>

            <div className="training-row">
              <label className="training-label">
                <input
                  type="checkbox"
                  checked={advGradCheckpoint}
                  onChange={(e) => setAdvGradCheckpoint(e.target.checked)}
                  style={{ marginRight: 6 }}
                />
                Grad checkpointing
              </label>
              <span className="training-hint">~30% less memory, ~10% slower. Flip on after an OOM.</span>
            </div>

            <div className="training-row">
              <label className="training-label">Early stop patience:</label>
              <input
                className="training-input training-input-num"
                type="number"
                min="0"
                max="20"
                value={advEarlyStopPatience}
                onChange={(e) => setAdvEarlyStopPatience(Math.max(0, Number(e.target.value) || 0))}
              />
              <span className="training-hint">
                Kill the run after N evals without val improvement. 0 disables. Default 3 ≈ 600 iters.
              </span>
            </div>

            <div className="training-row">
              <label className="training-label">Resume from:</label>
              <select
                className="training-select"
                value={advResumeFrom}
                onChange={(e) => setAdvResumeFrom(e.target.value)}
              >
                <option value="">— start fresh —</option>
                {adapters.filter((a) => a.has_weights).map((a) => (
                  <option key={a.path} value={`${a.path}/adapters.safetensors`}>
                    {a.name}
                  </option>
                ))}
              </select>
              <span className="training-hint">Pick up where a previous run left off (e.g. after OOM).</span>
            </div>
          </div>
        )}

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
              {progress.train_loss != null && (
                <span>
                  <span className="loss-dot loss-dot-train" /> train {progress.train_loss.toFixed(3)}
                </span>
              )}
              {progress.val_loss != null && (
                <span>
                  <span className="loss-dot loss-dot-val" /> val {progress.val_loss.toFixed(3)}
                </span>
              )}
              {progress.tokens_sec && <span>{progress.tokens_sec.toFixed(0)} tok/s</span>}
            </div>
            <div className="training-score-track" style={{ height: 8 }}>
              <div className="training-score-fill" style={{ width: `${progress.percent}%`, background: "var(--pink)", transition: "width 0.5s" }} />
            </div>
            <LossSparkline history={progress.loss_history || []} totalIters={progress.total_iters} />
            <OverfitHint
              trainLoss={progress.train_loss}
              valLoss={progress.val_loss}
            />
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

      <ConfirmModal
        open={confirmTraining}
        title="Training is resource-intensive"
        body={<>
          <p>
            This fine-tunes a LoRA adapter for{" "}
            <strong>{filterRepo || "Global (default fallback)"}</strong>{" "}
            on{" "}
            <strong>
              {selectedDataset
                ? (datasets.find((d) => d.path === selectedDataset)?.filename || "the selected dataset")
                : "the latest matching dataset"}
            </strong>
            {selectedDataset
              ? ` (${(datasets.find((d) => d.path === selectedDataset)?.examples || 0).toLocaleString()} examples)`
              : ""}
            . Runs locally — expect heavy GPU/CPU use for <strong>20-30 minutes</strong> and no interruption.
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
