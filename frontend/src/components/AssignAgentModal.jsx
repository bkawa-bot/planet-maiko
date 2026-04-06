import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { Bot, Star, Plus, Rocket } from "lucide-react";
import "./AssignAgentModal.css";

const AVATAR_EMOJI = {
  shiba: "🐕", corgi: "🐶", husky: "🐺", poodle: "🐩", golden: "🦮", beagle: "🐕‍🦺",
  dalmatian: "🐾", samoyed: "☁️", akita: "🐕", pomeranian: "🧸",
  calico_cat: "🐱", tabby_cat: "🐈", black_cat: "🐈‍⬛",
  bunny: "🐰", hamster: "🐹", fox: "🦊",
};

const RANK_LABELS = { pup: "🌱 Pup", junior: "⭐ Junior", senior: "🌟 Senior", expert: "👑 Expert" };

export default function AssignAgentModal({ task, onClose, onAssigned }) {
  const [recommendations, setRecommendations] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [repoPath, setRepoPath] = useState("");
  const [repoRoots, setRepoRoots] = useState([]);
  const [loading, setLoading] = useState(true);
  const [assigning, setAssigning] = useState(false);
  const [useWorktree, setUseWorktree] = useState(false);
  const [autoKickoff, setAutoKickoff] = useState(false);
  const [customPrompt, setCustomPrompt] = useState("");
  const [branchName, setBranchName] = useState("");

  useEffect(() => {
    const repo = task.metadata?.repo || task.extra?.repo || "";
    Promise.all([
      api.recommendAgent(repo),
      api.getConfig(),
    ]).then(([recs, cfg]) => {
      setRecommendations(recs);
      const validRecs = recs.filter(r => r.profile);
      if (validRecs.length > 0) setSelectedId(validRecs[0].profile.id);

      // Auto-fill repo path from repo_roots config
      const roots = cfg?.github?.repo_roots || [];
      setRepoRoots(roots);
      if (roots.length === 1 && repo) {
        // Single root + known repo name: auto-resolve
        const repoShort = repo.includes("/") ? repo.split("/").pop() : repo;
        setRepoPath(`${roots[0]}/${repoShort}`);
      } else if (roots.length === 1) {
        setRepoPath(roots[0]);
      }
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [task]);

  const handleCreateNew = async () => {
    try {
      const profile = await api.createProfile({});
      showToast(`${profile.display_name} just arrived! 🐾`, "normal");
      setRecommendations((prev) => [
        { profile, score: 0, reasons: ["brand new"] },
        ...prev,
      ]);
      setSelectedId(profile.id);
    } catch (err) {
      showToast("Couldn't create agent", "high");
    }
  };

  const handleAssign = async () => {
    if (!selectedId || !repoPath) {
      showToast("Select an agent and enter a repo path", "high");
      return;
    }
    setAssigning(true);
    try {
      await api.assignAgent({
        task_id: task.id,
        profile_id: selectedId,
        repo_path: repoPath,
        use_worktree: useWorktree,
        auto_kickoff: autoKickoff,
        custom_prompt: customPrompt || undefined,
        branch_name: branchName || undefined,
      });
      const agent = recommendations.find((r) => r.profile && r.profile.id === selectedId);
      showToast(`${agent?.profile?.display_name || "Agent"} assigned! Worktree ready.`, "normal");
      onAssigned();
      onClose();
    } catch (err) {
      showToast(err.message || "Assignment failed", "high");
    }
    setAssigning(false);
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="assign-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <Bot size={16} />
          <span>Assign Agent</span>
          <span className="assign-task-title">{task.title}</span>
        </div>

        <div className="modal-body">
          {loading ? (
            <p className="assign-loading">Finding the best match...</p>
          ) : (
            <>
              {recommendations.some((rec) => rec.gap_detected) && (
                <div style={{ padding: "8px 12px", marginBottom: 8, background: "var(--pink-soft)", borderRadius: "var(--radius-xs)", fontSize: 12, color: "var(--pink)" }}>
                  {recommendations.find((rec) => rec.gap_detected)?.reasons?.[0] || "No experienced agent for this task."}
                </div>
              )}
              <div className="assign-section-label">Recommended Agents</div>
              <div className="assign-agent-list">
                {recommendations.filter((rec) => rec.profile).map((rec) => (
                  <div
                    key={rec.profile.id}
                    className={`assign-agent-option ${selectedId === rec.profile.id ? "selected" : ""}`}
                    onClick={() => setSelectedId(rec.profile.id)}
                  >
                    <span className="assign-avatar">{AVATAR_EMOJI[rec.profile.avatar] || "🐕"}</span>
                    <div className="assign-agent-info">
                      <div className="assign-agent-name">
                        {rec.profile.display_name}
                        <span className="assign-agent-rank">{RANK_LABELS[rec.profile.rank] || "🌱 Pup"}</span>
                      </div>
                      <div className="assign-agent-reasons">
                        {rec.reasons.join(" · ") || "available"}
                      </div>
                    </div>
                    {rec.score > 0 && <span className="assign-score">{(rec.score * 100).toFixed(0)}%</span>}
                  </div>
                ))}
              </div>

              <button className="btn btn-sm" onClick={handleCreateNew} style={{ marginTop: 8 }}>
                <Plus size={10} /> Or create a new agent
              </button>

              <div className="assign-section-label" style={{ marginTop: 16 }}>Repository Path</div>
              <input
                className="assign-repo-input"
                type="text"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                placeholder={repoRoots.length ? `${repoRoots[0]}/repo-name` : "/path/to/your/repo"}
              />
              {!repoPath && repoRoots.length === 0 && (
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                  Set repository roots in Settings to auto-fill this field.
                </div>
              )}

              <div className="assign-section-label" style={{ marginTop: 16 }}>Branch Name (optional)</div>
              <input
                className="assign-repo-input"
                type="text"
                value={branchName}
                onChange={(e) => setBranchName(e.target.value)}
                placeholder="maiko/fix-auth-bug (auto-generated if blank)"
              />

              <div className="assign-section-label" style={{ marginTop: 16 }}>Options</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-dim)", cursor: "pointer" }}>
                  <input type="checkbox" checked={useWorktree} onChange={(e) => setUseWorktree(e.target.checked)} />
                  Use git worktree
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    Agent works in an isolated copy of the repo
                  </span>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-dim)", cursor: "pointer" }}>
                  <input type="checkbox" checked={autoKickoff} onChange={(e) => setAutoKickoff(e.target.checked)} />
                  Auto-start agent
                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                    Launch the agent immediately after setup
                  </span>
                </label>
              </div>

              <div className="assign-section-label" style={{ marginTop: 16 }}>Additional Instructions (optional)</div>
              <textarea
                style={{
                  width: "100%", minHeight: 60, padding: "8px 10px", fontSize: 12,
                  border: "1px solid var(--border)", borderRadius: "var(--radius-xs)",
                  background: "var(--bg)", color: "var(--text)", fontFamily: "var(--font)",
                  resize: "vertical",
                }}
                value={customPrompt}
                onChange={(e) => setCustomPrompt(e.target.value)}
                placeholder="e.g. Write tests first. Use the existing error handling patterns in src/utils/errors.py. Follow the PR template."
              />
            </>
          )}
        </div>

        <div className="assign-footer">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleAssign} disabled={assigning || !selectedId || !repoPath}>
            <Rocket size={12} /> {assigning ? "Preparing..." : autoKickoff ? "Assign & Launch" : "Assign & Prepare"}
          </button>
        </div>
      </div>
    </div>
  );
}
