import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Users, Network, Crown, RefreshCw, Activity } from "lucide-react";
import "./Team.css";

export default function Team() {
  const [tab, setTab] = useState("expertise");
  const [graph, setGraph] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getExpertise().then(setGraph).catch(console.error).finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="page-empty">Loading...</p>;

  const contributors = graph?.expertise ? Object.entries(graph.expertise) : [];

  // Build repo → experts map
  const repoExperts = {};
  for (const [author, repos] of contributors) {
    for (const [repo, data] of Object.entries(repos)) {
      if (!repoExperts[repo]) repoExperts[repo] = [];
      repoExperts[repo].push({ author, ...data });
    }
  }
  // Sort experts within each repo by score
  for (const repo of Object.keys(repoExperts)) {
    repoExperts[repo].sort((a, b) => b.score - a.score);
  }

  return (
    <div className="team-page">
      <div className="team-tabs">
        <button className={`inbox-tab ${tab === "activity" ? "active" : ""}`} onClick={() => setTab("activity")}>
          <Activity size={12} /> Activity
        </button>
        <button className={`inbox-tab ${tab === "expertise" ? "active" : ""}`} onClick={() => setTab("expertise")}>
          <Network size={12} /> Expertise
        </button>
      </div>

      {tab === "activity" && (
        <div className="team-activity">
          <div className="empty-state">
            <Users size={36} className="empty-icon" />
            <div className="empty-title">No team activity data yet</div>
            <div className="empty-sub">Run the team dashboard skill to generate a report</div>
            <button className="btn btn-primary" style={{ marginTop: 12 }}>
              <RefreshCw size={12} /> Run Team Dashboard
            </button>
          </div>
        </div>
      )}

      {tab === "expertise" && (
        <div className="expertise-view">
          {contributors.length === 0 ? (
            <div className="empty-state">
              <Network size={36} className="empty-icon" />
              <div className="empty-title">No expertise data yet</div>
              <div className="empty-sub">Builds automatically from PR and review history</div>
            </div>
          ) : (
            <>
              {/* By Person */}
              <h3>By Person</h3>
              <div className="person-grid">
                {contributors.map(([author, repos]) => {
                  const repoEntries = Object.entries(repos).sort((a, b) => b[1].score - a[1].score);
                  const topRepo = repoEntries[0];
                  return (
                    <div key={author} className="person-card card">
                      <div className="person-name">{author}</div>
                      <div className="person-stats">
                        {repoEntries.length} repos
                        {topRepo && <span className="tag">{topRepo[0].split("/").pop()}</span>}
                      </div>
                      <div className="person-bars">
                        {repoEntries.slice(0, 5).map(([repo, data]) => {
                          const maxScore = repoEntries[0][1].score || 1;
                          const pct = Math.round((data.score / maxScore) * 100);
                          const daysAgo = Math.round((Date.now() - new Date(data.last_active).getTime()) / 86400000);
                          return (
                            <div key={repo} className="bar-row">
                              <span className="bar-label">{repo.split("/").pop()}</span>
                              <div className="bar-track">
                                <div className="bar-fill" style={{ width: `${pct}%` }} />
                              </div>
                              <span className="bar-recency">{daysAgo === 0 ? "today" : `${daysAgo}d`}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* By Repository */}
              <h3 style={{ marginTop: 20 }}>By Repository</h3>
              <div className="repo-list">
                {Object.entries(repoExperts).sort((a, b) => b[1][0].score - a[1][0].score).map(([repo, experts]) => (
                  <div key={repo} className="repo-card card">
                    <div className="repo-name">{repo}</div>
                    <div className="repo-experts">
                      {experts.map((e, i) => {
                        const daysAgo = Math.round((Date.now() - new Date(e.last_active).getTime()) / 86400000);
                        return (
                          <span key={e.author} className={`expert-chip ${i === 0 ? "lead" : ""}`}>
                            {i === 0 && <Crown size={10} />}
                            {e.author}
                            <span className="expert-recency">{daysAgo === 0 ? "today" : `${daysAgo}d`}</span>
                          </span>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
