import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import InfoButton from "../components/InfoButton";
import {
  GraduationCap, Bot, GitPullRequest, Play, Loader, RefreshCw,
  Award, ChevronDown, ChevronRight, CheckSquare, Clock, X,
} from "lucide-react";
import "./Training.css";

function scoreColor(score) {
  if (score == null) return "var(--text-muted)";
  if (score < 4) return "var(--urgent)";
  if (score < 6) return "var(--high)";
  if (score < 8) return "var(--lemon)";
  return "var(--green)";
}

export default function Training() {
  const [prs, setPrs] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [loadingPRs, setLoadingPRs] = useState(false);
  const [selectedPR, setSelectedPR] = useState(null);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState([]);
  const [expandedHistory, setExpandedHistory] = useState(null);

  const fetchPRs = async () => {
    setLoadingPRs(true);
    try {
      setPrs(await api.getTrainingPRs());
    } catch (err) {
      showToast("Could not load PRs: " + err.message, "high");
    }
    setLoadingPRs(false);
  };

  const fetchHistory = () => api.getTrainingHistory().then(setHistory).catch(() => {});

  useEffect(() => {
    fetchPRs();
    api.getProfiles().then(setProfiles).catch(() => {});
    fetchHistory();
  }, []);

  const handleRun = async () => {
    if (!selectedPR || !selectedAgent) return;
    setRunning(true);
    setResult(null);
    try {
      const res = await api.runTraining({
        repo: selectedPR.repo,
        pr_number: selectedPR.number,
        agent_profile_id: selectedAgent,
      });
      setResult(res);
      showToast(`Training complete! Winner: ${res.winner}`, "normal");
      api.getProfiles().then(setProfiles).catch(() => {});
      fetchHistory();
    } catch (err) {
      showToast("Training failed: " + err.message, "high");
    }
    setRunning(false);
  };

  const agentProfile = profiles.find((p) => p.id === selectedAgent);

  return (
    <div className="training-page">
      <div className="training-header">
        <h2><GraduationCap size={18} /> Training</h2>
        <InfoButton title={<><GraduationCap size={16} /> Agent Training</>}>
          <p>Training builds an agent's <em>context set</em> — the specific learnings it uses when working on tasks.</p>
          <h4>How it works</h4>
          <ol>
            <li><strong>Pick a merged PR</strong> — this is the "ground truth" answer.</li>
            <li><strong>Pick an agent</strong> — pups get random learning combos, specialists get variations of their current set.</li>
            <li><strong>Run training</strong> — each combo generates code for the task, then an LLM judge scores them against the actual merged code.</li>
            <li><strong>Winner is saved</strong> — the best-scoring combo becomes the agent's context set.</li>
          </ol>
          <h4>Pups vs Specialists</h4>
          <p>Pups (new agents) get 3 random combos to discover what works. Specialists test small tweaks to their proven set — adding a new learning or dropping a weak one.</p>
        </InfoButton>
      </div>

      <div className="training-form">
        {/* PR selector */}
        <div className="training-section">
          <label className="training-label">
            <GitPullRequest size={12} /> Select a merged PR
          </label>
          <div className="training-pr-controls">
            <button className="btn btn-sm" onClick={fetchPRs} disabled={loadingPRs}>
              {loadingPRs ? <Loader size={10} className="spin" /> : <RefreshCw size={10} />} Refresh
            </button>
          </div>
          {prs.length === 0 && !loadingPRs && (
            <div className="training-empty">No merged PRs found. Configure repos in Settings &gt; GitHub.</div>
          )}
          <div className="training-pr-list">
            {prs.map((pr) => (
              <div
                key={`${pr.repo}-${pr.number}`}
                className={`training-pr-item ${selectedPR?.repo === pr.repo && selectedPR?.number === pr.number ? "selected" : ""}`}
                onClick={() => setSelectedPR(pr)}
              >
                <span className="training-pr-repo">{pr.repo}</span>
                <span className="training-pr-number">#{pr.number}</span>
                <span className="training-pr-title">{pr.title}</span>
                {pr.merged_at && <span className="training-pr-date">{new Date(pr.merged_at).toLocaleDateString()}</span>}
              </div>
            ))}
          </div>
        </div>

        {/* Agent selector */}
        <div className="training-section">
          <label className="training-label">
            <Bot size={12} /> Select an agent
          </label>
          <select
            className="training-select"
            value={selectedAgent}
            onChange={(e) => setSelectedAgent(e.target.value)}
          >
            <option value="">Choose an agent...</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.display_name} ({p.rank || "pup"}) — {p.context_set?.length || 0} learnings
              </option>
            ))}
          </select>
          {agentProfile && (
            <div className="training-agent-info">
              <span className="tag">{agentProfile.rank || "pup"}</span>
              <span>{agentProfile.context_set?.length || 0} learnings in context set</span>
              {(agentProfile.context_set?.length || 0) === 0 && (
                <span className="training-pup-note">Pup — will try 3 random combos</span>
              )}
            </div>
          )}
        </div>

        {/* Run button */}
        <button
          className="btn btn-primary training-run-btn"
          onClick={handleRun}
          disabled={running || !selectedPR || !selectedAgent}
        >
          {running ? <><Loader size={12} className="spin" /> Training...</> : <><Play size={12} /> Run Training</>}
        </button>
      </div>

      {/* Results */}
      {result && (
        <div className="training-results">
          <div className="training-results-header">
            <GraduationCap size={14} />
            <span>Results: {result.pr?.title}</span>
            {result.tags?.length > 0 && (
              <div className="training-tags">
                {result.tags.map((t) => <span key={t} className="tag">{t}</span>)}
              </div>
            )}
          </div>

          <div className="training-entries">
            {(result.entries || []).map((entry, i) => {
              const isWinner = entry.name === result.winner;
              return (
                <div key={i} className={`training-entry ${isWinner ? "is-winner" : ""} ${entry.name === "baseline" ? "is-baseline" : ""}`}>
                  <div className="training-entry-header">
                    <span className="training-entry-name">
                      {entry.name === "baseline" ? "baseline (no learnings)" : entry.name}
                    </span>
                    {isWinner && <span className="training-winner-tag"><Award size={10} /> Winner</span>}
                    <span className="training-entry-count">{entry.learning_ids?.length || 0} learnings</span>
                  </div>
                  <div className="training-score-row">
                    <div className="training-score-track">
                      <div className="training-score-fill" style={{ width: `${(entry.score || 0) * 10}%`, background: scoreColor(entry.score) }} />
                    </div>
                    <span className="training-score-val" style={{ color: scoreColor(entry.score) }}>
                      {entry.score != null ? entry.score.toFixed(1) : "--"}
                    </span>
                  </div>
                  {entry.reason && <div className="training-entry-reason">{entry.reason}</div>}
                </div>
              );
            })}
          </div>

          <div className="training-summary">
            <CheckSquare size={12} />
            <span>
              {result.agent}'s context set updated: {result.context_set_before?.length || 0} → {result.context_set_after?.length || 0} learnings
            </span>
          </div>
        </div>
      )}

      {/* Training History */}
      {history.length > 0 && (
        <div className="training-history">
          <div className="training-history-title"><Clock size={14} /> Training History</div>
          {history.map((t) => {
            const isExpanded = expandedHistory === t.id;
            const agentName = t.entries?.[0]?.agent_profile_id
              ? profiles.find((p) => p.id === t.entries[0].agent_profile_id)?.display_name
              : null;
            return (
              <div key={t.id} className="training-history-item" onClick={() => setExpandedHistory(isExpanded ? null : t.id)}>
                <div className="training-history-header">
                  {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  <span className="training-history-pr">{t.pr_repo} #{t.pr_number}</span>
                  <span className="training-history-pr-title">{t.pr_title}</span>
                  {agentName && <span className="tag"><Bot size={9} /> {agentName}</span>}
                  {t.winning_strategy && <span className="training-history-winner"><Award size={9} /> {t.winning_strategy}</span>}
                  <span className="training-history-date">{t.created_at ? new Date(t.created_at).toLocaleDateString() : ""}</span>
                </div>
                {isExpanded && t.entries?.length > 0 && (
                  <div className="training-entries" style={{ marginTop: 8 }}>
                    {t.entries.map((entry) => {
                      const isWinner = entry.strategy === t.winning_strategy;
                      return (
                        <div key={entry.id} className={`training-entry ${isWinner ? "is-winner" : ""} ${entry.strategy === "baseline" ? "is-baseline" : ""}`}>
                          <div className="training-entry-header">
                            <span className="training-entry-name">{entry.strategy}</span>
                            {isWinner && <span className="training-winner-tag"><Award size={10} /> Winner</span>}
                            <span className="training-entry-count">{entry.learning_ids?.length || 0} learnings</span>
                          </div>
                          <div className="training-score-row">
                            <div className="training-score-track">
                              <div className="training-score-fill" style={{ width: `${(entry.score || 0) * 10}%`, background: scoreColor(entry.score) }} />
                            </div>
                            <span className="training-score-val" style={{ color: scoreColor(entry.score) }}>
                              {entry.score != null ? entry.score.toFixed(1) : "--"}
                            </span>
                          </div>
                          {entry.judge_reasoning && <div className="training-entry-reason">{entry.judge_reasoning}</div>}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
