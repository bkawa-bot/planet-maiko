import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Trophy, Play, ChevronDown, ChevronRight, Award,
  BarChart3, Clock, X, Loader, Bot, Crown, TrendingUp, TrendingDown, Minus,
} from "lucide-react";
import InfoButton from "../components/InfoButton";
import "./Tournaments.css";

function scoreColor(score) {
  if (score == null) return "var(--text-muted)";
  const t = score / 10;
  if (t < 0.4) return "var(--urgent)";
  if (t < 0.6) return "var(--high)";
  if (t < 0.8) return "var(--lemon)";
  return "var(--green)";
}

function ScoreBar({ score, maxScore = 10, trackClass, fillClass }) {
  const pct = score != null ? Math.round((score / maxScore) * 100) : 0;
  return (
    <div className={trackClass}>
      <div
        className={fillClass}
        style={{ width: `${pct}%`, background: scoreColor(score) }}
      />
    </div>
  );
}

function StrategyEntry({ entry, isWinner }) {
  const [showReasoning, setShowReasoning] = useState(false);
  const isBaseline = entry.strategy === "baseline";

  return (
    <div className={`strategy-entry${isWinner ? " is-winner" : ""}${isBaseline ? " is-baseline" : ""}`}>
      <div className="strategy-header">
        {isBaseline ? (
          <span className="strategy-name baseline-name">baseline (no learnings)</span>
        ) : (
          <span className="strategy-name">
            <Bot size={12} /> {entry.strategy}
          </span>
        )}
        {isWinner && <span className="strategy-winner-tag"><Award size={10} /> Winner</span>}
      </div>
      <div className="strategy-score-row">
        <ScoreBar
          score={entry.score}
          trackClass="strategy-score-track"
          fillClass="strategy-score-fill"
        />
        <span className="strategy-score-val" style={{ color: scoreColor(entry.score) }}>
          {entry.score != null ? entry.score.toFixed(1) : "--"}
        </span>
      </div>
      {entry.judge_reasoning && (
        <div
          className="strategy-reasoning"
          onClick={() => setShowReasoning(!showReasoning)}
        >
          {showReasoning ? "Hide reasoning" : "Show reasoning..."}
        </div>
      )}
      {showReasoning && entry.judge_reasoning && (
        <div className="strategy-reasoning-full">{entry.judge_reasoning}</div>
      )}
      {entry.learning_ids && entry.learning_ids.length > 0 && (
        <div className="strategy-learnings">
          <span className="learning-count">{entry.learning_ids.length} learnings</span>
        </div>
      )}
    </div>
  );
}

function TournamentCard({ tournament }) {
  const [expanded, setExpanded] = useState(false);
  const entries = tournament.entries || [];

  return (
    <div className="tournament-card">
      <div className="tournament-card-header" onClick={() => setExpanded(!expanded)}>
        {expanded
          ? <ChevronDown size={14} color="var(--pink)" />
          : <ChevronRight size={14} color="var(--text-muted)" />}
        <div className="tournament-pr-info">
          <div className="tournament-pr-title">{tournament.pr_title || "Untitled PR"}</div>
          <div className="tournament-pr-meta">
            <span>{tournament.pr_repo}</span>
            <span>#{tournament.pr_number}</span>
            {tournament.created_at && (
              <span>{new Date(tournament.created_at).toLocaleDateString()}</span>
            )}
          </div>
          {tournament.task_tags && tournament.task_tags.length > 0 && (
            <div className="tournament-tags">
              {tournament.task_tags.map((t) => (
                <span key={t} className="tag">{t}</span>
              ))}
            </div>
          )}
        </div>
        <span className={`badge ${tournament.status}`}>{tournament.status}</span>
        {tournament.winning_strategy && (
          <div className="tournament-winner">
            {tournament.winning_strategy === "baseline" ? (
              <><Award size={12} /> <span>baseline</span></>
            ) : (
              <><Bot size={12} /> <span>{tournament.winning_strategy}</span></>
            )}
          </div>
        )}
      </div>
      {expanded && entries.length > 0 && (
        <div className="tournament-entries">
          {entries.map((entry) => (
            <StrategyEntry
              key={entry.id}
              entry={entry}
              isWinner={entry.strategy === tournament.winning_strategy}
            />
          ))}
        </div>
      )}
      {expanded && entries.length === 0 && (
        <div className="tournament-entries">
          <p style={{ fontSize: 12, color: "var(--text-muted)", padding: "8px 0" }}>
            No entries recorded for this tournament.
          </p>
        </div>
      )}
    </div>
  );
}

function computeAgentLeaderboard(tournaments) {
  const agents = {};
  const baseline = { wins: 0, total: 0, totalScore: 0 };

  for (const t of tournaments) {
    if (t.status !== "completed") continue;
    for (const entry of (t.entries || [])) {
      if (entry.strategy === "baseline") {
        baseline.total++;
        baseline.totalScore += entry.score || 0;
        if (t.winning_strategy === "baseline") baseline.wins++;
        continue;
      }
      const key = entry.agent_profile_id || entry.strategy;
      if (!agents[key]) {
        agents[key] = { name: entry.strategy, id: entry.agent_profile_id, wins: 0, total: 0, totalScore: 0, bestScore: 0 };
      }
      agents[key].total++;
      agents[key].totalScore += entry.score || 0;
      if ((entry.score || 0) > agents[key].bestScore) agents[key].bestScore = entry.score || 0;
      if (t.winning_strategy === entry.strategy) agents[key].wins++;
    }
  }

  const baselineAvg = baseline.total > 0 ? baseline.totalScore / baseline.total : 0;

  const sorted = Object.values(agents)
    .map((a) => ({
      ...a,
      avgScore: a.total > 0 ? a.totalScore / a.total : 0,
      winRate: a.total > 0 ? a.wins / a.total : 0,
      vsBaseline: a.total > 0 ? (a.totalScore / a.total) - baselineAvg : 0,
    }))
    .sort((a, b) => b.wins - a.wins || b.avgScore - a.avgScore);

  return { agents: sorted, baselineAvg, baselineTotal: baseline.total };
}

const RANK_COLORS = ["#FFD700", "#C0C0C0", "#CD7F32"]; // gold, silver, bronze

function AgentLeaderboard({ tournaments }) {
  const { agents, baselineAvg, baselineTotal } = computeAgentLeaderboard(tournaments);

  if (agents.length === 0) return null;

  return (
    <div className="agent-leaderboard">
      <h3 className="agent-lb-title"><Trophy size={15} /> Agent Leaderboard</h3>
      <div className="agent-lb-list">
        {agents.map((a, i) => {
          const delta = a.vsBaseline;
          const isLeader = i === 0 && a.wins > 0;
          return (
            <div key={a.id || a.name} className={`agent-lb-row${isLeader ? " is-leader" : ""}`}>
              <div className="agent-lb-rank" style={i < 3 ? { color: RANK_COLORS[i] } : {}}>
                {isLeader ? <Crown size={14} /> : `#${i + 1}`}
              </div>
              <div className="agent-lb-identity">
                <Bot size={14} />
                <span className="agent-lb-name">{a.name}</span>
              </div>
              <div className="agent-lb-record">
                <span className="agent-lb-wins">{a.wins}W</span>
                <span className="agent-lb-losses">{a.total - a.wins}L</span>
              </div>
              <div className="agent-lb-score-col">
                <ScoreBar score={a.avgScore} trackClass="agent-lb-bar-track" fillClass="agent-lb-bar-fill" />
                <span className="agent-lb-score-val" style={{ color: scoreColor(a.avgScore) }}>
                  {a.avgScore.toFixed(1)}
                </span>
              </div>
              <div className={`agent-lb-delta ${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}`}>
                {delta > 0 ? <TrendingUp size={11} /> : delta < 0 ? <TrendingDown size={11} /> : <Minus size={11} />}
                <span>{delta >= 0 ? "+" : ""}{delta.toFixed(1)}</span>
              </div>
            </div>
          );
        })}
        {/* Baseline row */}
        <div className="agent-lb-row is-baseline">
          <div className="agent-lb-rank">--</div>
          <div className="agent-lb-identity">
            <span className="agent-lb-name baseline-label">baseline (no learnings)</span>
          </div>
          <div className="agent-lb-record">
            <span className="agent-lb-losses">{baselineTotal} runs</span>
          </div>
          <div className="agent-lb-score-col">
            <ScoreBar score={baselineAvg} trackClass="agent-lb-bar-track" fillClass="agent-lb-bar-fill" />
            <span className="agent-lb-score-val" style={{ color: scoreColor(baselineAvg) }}>
              {baselineAvg.toFixed(1)}
            </span>
          </div>
          <div className="agent-lb-delta">
            <Minus size={11} />
            <span>ref</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Tournaments() {
  const [tournaments, setTournaments] = useState([]);
  const [scores, setScores] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showRunModal, setShowRunModal] = useState(false);
  const [runRepo, setRunRepo] = useState("");
  const [runPR, setRunPR] = useState("");
  const [running, setRunning] = useState(false);
  const [showLearningLB, setShowLearningLB] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [t, s] = await Promise.all([
        api.getTournaments(),
        api.getTournamentScores(),
      ]);
      setTournaments(Array.isArray(t) ? t : t.tournaments || []);
      if (Array.isArray(s)) {
        setScores(s);
      } else if (s && typeof s === "object") {
        const arr = Object.entries(s).map(([id, data]) => ({
          id,
          avg_score: data.avg_score,
          tournament_count: data.tournament_count,
          rule: data.rule || id.slice(0, 12),
        }));
        arr.sort((a, b) => (b.avg_score || 0) - (a.avg_score || 0));
        setScores(arr);
      }
    } catch (err) {
      console.error("Failed to load tournaments:", err);
    }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  const handleRun = async () => {
    if (!runRepo.trim() || !runPR.trim()) return;
    setRunning(true);
    try {
      await api.runTournament(runRepo.trim(), parseInt(runPR, 10));
      setShowRunModal(false);
      setRunRepo("");
      setRunPR("");
      fetchData();
    } catch (err) {
      console.error("Tournament run failed:", err);
    }
    setRunning(false);
  };

  if (loading) return <p className="page-empty">Loading tournaments...</p>;

  return (
    <div className="tournaments-page">
      <div className="tournaments-header">
        <h2><Trophy size={18} /> Tournaments</h2>
        <InfoButton title={<><Trophy size={16} /> How Tournaments Work</>}>
          <p>Tournaments are how Planet Maiko learns which agent produces the best code for different tasks.</p>
          <h4>How it works</h4>
          <ol>
            <li><strong>A PR gets merged</strong> — this becomes the "ground truth" answer.</li>
            <li><strong>Each agent competes</strong> — they each get the task description plus their personalized set of learnings (their context set).</li>
            <li><strong>A baseline runs too</strong> — with zero learnings, so we can measure how much context actually helps.</li>
            <li><strong>LLM-as-judge scores outputs</strong> — comparing each agent's output against the actual merged code.</li>
            <li><strong>Scores feed back</strong> — winners get specialization boosts, learnings in winning sets get confidence bumps.</li>
          </ol>
          <h4>Why it matters</h4>
          <p>Since agents <em>are</em> context sets, tournaments answer: "which agent's learning selection works best for this type of task?" Over time, agents naturally specialize.</p>
          <h4>The leaderboard</h4>
          <p>The "vs baseline" column shows how much each agent's context set helps compared to running with no learnings at all.</p>
        </InfoButton>
        <span style={{ flex: 1 }} />
        <button className="btn btn-primary" onClick={() => setShowRunModal(true)}>
          <Play size={12} /> Run Tournament
        </button>
      </div>

      {/* Agent Leaderboard */}
      <AgentLeaderboard tournaments={tournaments} />

      {/* Learning Leaderboard (collapsible) */}
      {scores.length > 0 && (
        <div className="leaderboard-section learning-lb">
          <h3 onClick={() => setShowLearningLB(!showLearningLB)} style={{ cursor: "pointer" }}>
            {showLearningLB ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <BarChart3 size={14} /> Learning Leaderboard
          </h3>
          {showLearningLB && (
            <table className="leaderboard-table">
              <thead>
                <tr>
                  <th>Learning</th>
                  <th>Avg Score</th>
                  <th>Tournaments</th>
                </tr>
              </thead>
              <tbody>
                {scores.slice(0, 15).map((s) => (
                  <tr key={s.id}>
                    <td className="leaderboard-rule">{s.rule}</td>
                    <td>
                      <div className="leaderboard-score-bar">
                        <ScoreBar
                          score={(s.avg_score || 0) * 10}
                          trackClass="leaderboard-bar-track"
                          fillClass="leaderboard-bar-fill"
                        />
                        <span
                          className="leaderboard-score-val"
                          style={{ color: scoreColor((s.avg_score || 0) * 10) }}
                        >
                          {s.avg_score != null ? (s.avg_score * 10).toFixed(1) : "--"}
                        </span>
                      </div>
                    </td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                      {s.tournament_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Recent Tournaments */}
      <div className="section-header">
        <Clock size={14} /> Recent Tournaments
      </div>
      {tournaments.length === 0 ? (
        <div className="empty-state">
          <Trophy size={36} className="empty-icon" />
          <div className="empty-title">No tournaments yet</div>
          <div className="empty-sub">
            Tournaments run automatically on merged PRs, or you can trigger one manually.
          </div>
        </div>
      ) : (
        <div className="tournament-list">
          {tournaments.map((t) => (
            <TournamentCard key={t.id} tournament={t} />
          ))}
        </div>
      )}

      {/* Run Tournament Modal */}
      {showRunModal && (
        <div className="modal-overlay" onClick={() => setShowRunModal(false)}>
          <div className="run-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Trophy size={16} />
              Run Tournament
              <span style={{ flex: 1 }} />
              <button
                className="btn btn-sm"
                onClick={() => setShowRunModal(false)}
                style={{ border: "none", padding: 4 }}
              >
                <X size={14} />
              </button>
            </div>
            <div className="modal-body">
              <label>
                Repository (org/repo)
                <input
                  value={runRepo}
                  onChange={(e) => setRunRepo(e.target.value)}
                  placeholder="e.g. octocat/hello-world"
                />
              </label>
              <label>
                PR Number
                <input
                  type="number"
                  value={runPR}
                  onChange={(e) => setRunPR(e.target.value)}
                  placeholder="e.g. 42"
                  onKeyDown={(e) => e.key === "Enter" && handleRun()}
                />
              </label>
            </div>
            <div className="run-modal-actions">
              <button className="btn" onClick={() => setShowRunModal(false)}>Cancel</button>
              <button
                className="btn btn-primary"
                onClick={handleRun}
                disabled={running || !runRepo.trim() || !runPR.trim()}
              >
                {running ? <><Loader size={12} className="spin" /> Running...</> : <><Play size={12} /> Run</>}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
