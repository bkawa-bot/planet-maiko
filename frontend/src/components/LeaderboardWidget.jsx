import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Trophy, Bot, Crown, CheckSquare, GitPullRequest } from "lucide-react";

const MEDAL_COLORS = ["#FFD700", "#C0C0C0", "#CD7F32"];

export default function LeaderboardWidget() {
  const [profiles, setProfiles] = useState([]);

  useEffect(() => {
    api.getProfiles().then((p) => setProfiles(p || [])).catch(() => {});
  }, []);

  // Rank by tasks completed, then PRs merged as the tiebreaker. No
  // success-rate ranking — "failing" a task is a fuzzy concept given
  // most tasks now end by user completion, not a pass/fail judgement.
  const ranked = profiles
    .filter((p) => (p.tasks_completed || 0) > 0)
    .sort((a, b) =>
      (b.tasks_completed || 0) - (a.tasks_completed || 0)
      || (b.prs_merged || 0) - (a.prs_merged || 0),
    );

  if (ranked.length === 0) return null;

  return (
    <div className="home-widget lb-widget">
      <div className="widget-header">
        <Trophy size={12} /> Leaderboard
      </div>
      <div className="lb-widget-list">
        {ranked.slice(0, 5).map((p, i) => {
          const isLeader = i === 0;
          return (
            <div key={p.id} className={`lb-widget-row ${isLeader ? "is-leader" : ""}`}>
              <span className="lb-widget-rank" style={i < 3 ? { color: MEDAL_COLORS[i] } : {}}>
                {isLeader ? <Crown size={12} /> : `#${i + 1}`}
              </span>
              <Bot size={12} />
              <span className="lb-widget-name">{p.display_name}</span>
              <span className="lb-widget-tasks"><CheckSquare size={9} /> {p.tasks_completed}</span>
              <span className="lb-widget-prs"><GitPullRequest size={9} /> {p.prs_merged}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
