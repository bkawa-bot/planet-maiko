import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { Bot, Plus, Rocket, Code2, Eye, Search } from "lucide-react";
import "./AssignAgentModal.css";

const AVATAR_EMOJI = {
  shiba: "🐕", corgi: "🐶", husky: "🐺", poodle: "🐩", golden: "🦮", beagle: "🐕‍🦺",
  dalmatian: "🐾", samoyed: "☁️", akita: "🐕", pomeranian: "🧸",
  calico_cat: "🐱", tabby_cat: "🐈", black_cat: "🐈‍⬛",
  bunny: "🐰", hamster: "🐹", fox: "🦊",
};

// Role chip metadata so the modal can show at-a-glance what each
// listed agent actually does — a Reviewer shouldn't get picked for a
// coding task, and vice versa.
const ROLE_META = {
  coding: { icon: Code2, label: "Coder" },
  review: { icon: Eye, label: "Reviewer" },
  investigation: { icon: Search, label: "Investigator" },
};

// Task type → expected agent role. Drives the default filter.
const TYPE_TO_ROLE = {
  review: "review",
  pr_review: "review",
  investigation: "investigation",
  repo_analysis: "investigation",
};

export default function AssignAgentModal({ task, onClose, onAssigned }) {
  const [profiles, setProfiles] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [repoPath, setRepoPath] = useState("");
  const [repoRoots, setRepoRoots] = useState([]);
  const [loading, setLoading] = useState(true);
  const [assigning, setAssigning] = useState(false);
  const [planFirst, setPlanFirst] = useState(false);
  const [customPrompt, setCustomPrompt] = useState("");
  const [branchName, setBranchName] = useState("");

  const repo = task.metadata?.repo || task.extra?.repo || "";
  const expectedRole = TYPE_TO_ROLE[task.type] || "coding";

  useEffect(() => {
    Promise.all([
      // Filter agents to the role this task needs, scoped to this
      // repo (or global). Backend falls back to all non-archived
      // when no matches exist.
      api.getProfiles({ role: expectedRole, ...(repo ? { repo } : {}) }),
      api.getConfig(),
    ]).then(([ps, cfg]) => {
      setProfiles(ps || []);
      if (ps && ps.length > 0) setSelectedId(ps[0].id);

      const roots = cfg?.github?.repo_roots || [];
      setRepoRoots(roots);
      if (roots.length === 1 && repo) {
        const repoShort = repo.includes("/") ? repo.split("/").pop() : repo;
        setRepoPath(`${roots[0]}/${repoShort}`);
      } else if (roots.length === 1) {
        setRepoPath(roots[0]);
      }
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [task, expectedRole, repo]);

  const handleCreateNew = async () => {
    try {
      const profile = await api.createProfile({ role: expectedRole, scope_repo: repo || null });
      showToast(`${profile.display_name} just arrived!`, "normal");
      setProfiles((prev) => [profile, ...prev]);
      setSelectedId(profile.id);
    } catch (err) {
      showToast("Couldn't create agent", "high");
    }
  };

  const handleAssign = async () => {
    if (!selectedId) {
      showToast("Select an agent", "high");
      return;
    }
    // Coding agents still need a repo path the user chooses. Review
    // and investigation agents resolve the path from config.
    if (expectedRole === "coding" && !repoPath) {
      showToast("Enter a repo path", "high");
      return;
    }
    setAssigning(true);
    try {
      await api.assignAgent({
        task_id: task.id,
        profile_id: selectedId,
        repo_path: repoPath,
        plan_first: planFirst,
        custom_prompt: customPrompt || undefined,
        branch_name: branchName || undefined,
      });
      const agent = profiles.find((p) => p.id === selectedId);
      showToast(`${agent?.display_name || "Agent"} assigned!`, "normal");
      onAssigned();
      onClose();
    } catch (err) {
      showToast(err.message || "Assignment failed", "high");
    }
    setAssigning(false);
  };

  const isCoding = expectedRole === "coding";

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
            <p className="assign-loading">Loading agents...</p>
          ) : (
            <>
              {profiles.length === 0 && (
                <div style={{ padding: "8px 12px", marginBottom: 8, background: "var(--pink-soft)", borderRadius: "var(--radius-xs)", fontSize: 12, color: "var(--pink)" }}>
                  No {ROLE_META[expectedRole]?.label.toLowerCase() || expectedRole} agent for this task yet. Create one below.
                </div>
              )}
              <div className="assign-section-label">
                Available {ROLE_META[expectedRole]?.label || expectedRole}s
              </div>
              <div className="assign-agent-list">
                {profiles.map((p) => {
                  const meta = ROLE_META[p.role] || ROLE_META.coding;
                  const RoleIcon = meta.icon;
                  return (
                    <div
                      key={p.id}
                      className={`assign-agent-option ${selectedId === p.id ? "selected" : ""}`}
                      onClick={() => setSelectedId(p.id)}
                    >
                      <span className="assign-avatar">{AVATAR_EMOJI[p.avatar] || "🐕"}</span>
                      <div className="assign-agent-info">
                        <div className="assign-agent-name">
                          {p.display_name}
                          <span className="assign-agent-role">
                            <RoleIcon size={10} /> {meta.label}
                          </span>
                          {p.scope_repo && <span className="assign-agent-scope">{p.scope_repo}</span>}
                          {!p.scope_repo && <span className="assign-agent-scope">global</span>}
                        </div>
                        {p.flavor_text && <div className="assign-agent-reasons">{p.flavor_text}</div>}
                      </div>
                    </div>
                  );
                })}
              </div>

              <button className="btn btn-sm" onClick={handleCreateNew} style={{ marginTop: 8 }}>
                <Plus size={10} /> Create a new {ROLE_META[expectedRole]?.label.toLowerCase() || "agent"}
              </button>

              {isCoding && (
                <>
                  <div className="assign-section-label" style={{ marginTop: 16 }}>Repository Path</div>
                  <input
                    className="assign-repo-input"
                    type="text"
                    value={repoPath}
                    onChange={(e) => setRepoPath(e.target.value)}
                    placeholder={repoRoots.length ? `${repoRoots[0]}/repo-name` : "/path/to/your/repo"}
                  />

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
                      <input type="checkbox" checked={planFirst} onChange={(e) => setPlanFirst(e.target.checked)} />
                      Plan first
                      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                        Agent proposes a plan for your approval before writing code
                      </span>
                    </label>
                  </div>
                  <div style={{ marginTop: 12, padding: "8px 10px", background: "var(--pink-soft)", borderRadius: "var(--radius-xs)", fontSize: 11, color: "var(--text-dim)" }}>
                    Maiko prepares an isolated worktree, launches the agent in the background, and drops a pupdate when it's ready for review.
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
                    placeholder="e.g. Write tests first. Use the existing error handling patterns in src/utils/errors.py."
                  />
                </>
              )}

              {!isCoding && (
                <div style={{ marginTop: 16, padding: "10px 12px", background: "var(--pink-soft)", borderRadius: "var(--radius-xs)", fontSize: 12, color: "var(--text-dim)" }}>
                  {ROLE_META[expectedRole]?.label || "This agent"} runs autonomously — Maiko prepares a worktree and starts it in the background. You'll see the result in your inbox.
                </div>
              )}
            </>
          )}
        </div>

        <div className="assign-footer">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleAssign} disabled={assigning || !selectedId || (isCoding && !repoPath)}>
            <Rocket size={12} /> {assigning ? "Preparing..." : "Assign"}
          </button>
        </div>
      </div>
    </div>
  );
}
