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
  const [loading, setLoading] = useState(true);
  const [assigning, setAssigning] = useState(false);

  useEffect(() => {
    const repo = task.metadata?.repo || task.extra?.repo || "";
    setRepoPath(repo);
    api.recommendAgent(repo).then((recs) => {
      setRecommendations(recs);
      if (recs.length > 0) setSelectedId(recs[0].profile.id);
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
      });
      const agent = recommendations.find((r) => r.profile.id === selectedId);
      showToast(`${agent?.profile.display_name || "Agent"} assigned! Worktree ready.`, "normal");
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
              <div className="assign-section-label">Recommended Agents</div>
              <div className="assign-agent-list">
                {recommendations.map((rec) => (
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

              <div className="assign-section-label" style={{ marginTop: 16 }}>Repo Path</div>
              <input
                className="assign-repo-input"
                type="text"
                value={repoPath}
                onChange={(e) => setRepoPath(e.target.value)}
                placeholder="/path/to/your/repo"
              />
            </>
          )}
        </div>

        <div className="assign-footer">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleAssign} disabled={assigning || !selectedId || !repoPath}>
            <Rocket size={12} /> {assigning ? "Preparing..." : "Assign & Prepare"}
          </button>
        </div>
      </div>
    </div>
  );
}
