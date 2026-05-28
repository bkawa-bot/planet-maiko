import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { Bot, Plus, Rocket } from "@icons";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import CardAvatar from "./CardAvatar";
import ModalPortal from "./ModalPortal";
import { useAgentTypes, roleMeta } from "../hooks/useAgentTypes";
import "./AssignAgentModal.css";

// Task type → expected agent role. Drives the default filter.
const TYPE_TO_ROLE = {
  review: "review",
  pr_review: "review",
  investigation: "investigation",
  repo_analysis: "investigation",
};

export default function AssignAgentModal({ task, onClose, onAssigned }) {
  const defaultOrg = useDefaultOrg();
  const agentTypes = useAgentTypes();
  const [profiles, setProfiles] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [specialtyId, setSpecialtyId] = useState("");
  const [specialties, setSpecialties] = useState([]);
  const [repoPath, setRepoPath] = useState("");
  const [repoRoots, setRepoRoots] = useState([]);
  const [loading, setLoading] = useState(true);
  const [assigning, setAssigning] = useState(false);
  const [planFirst, setPlanFirst] = useState(false);
  const [customPrompt, setCustomPrompt] = useState("");
  const [branchName, setBranchName] = useState("");
  // One-sentence ritual: capture the user's intent explicitly before
  // the agent gets the task. Prefilled from task.metadata.description or
  // body if present; the user can amend. Saved onto task.metadata on
  // assign so build_task_prompt renders it in TASK.md.
  const initialIntent = (task.metadata?.description || task.metadata?.body || task.metadata?.description || task.metadata?.body || "").trim();
  const [intent, setIntent] = useState(initialIntent);
  const [nonGoals, setNonGoals] = useState((task.metadata?.non_goals || "").trim());

  const repo = task.metadata?.repo || "";
  const expectedRole = TYPE_TO_ROLE[task.type] || "coding";

  useEffect(() => {
    Promise.all([
      // Filter agents to the role this task needs, scoped to this
      // repo (or global). Backend falls back to all non-archived
      // when no matches exist.
      api.getProfiles({ role: expectedRole, ...(repo ? { repo } : {}) }),
      api.getConfig(),
      // Specialty names for the per-agent dropdown. Silent on failure —
      // the dropdown just shows bare IDs or stays hidden.
      api.getSkills().catch(() => []),
    ]).then(([ps, cfg, skills]) => {
      setProfiles(ps || []);
      if (ps && ps.length > 0) setSelectedId(ps[0].id);
      setSpecialties(Array.isArray(skills) ? skills : []);

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

  // When the user switches between agents, drop any specialty picked
  // for the previous agent — the new agent may not have it attached.
  useEffect(() => { setSpecialtyId(""); }, [selectedId]);

  const selectedProfile = profiles.find((p) => p.id === selectedId) || null;
  const attachedSpecialties = (selectedProfile?.specialty_ids || [])
    .map((sid) => specialties.find((s) => s.id === sid))
    .filter(Boolean);

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

  const isReassign = !!task.assigned_agent_id;

  const handleAssign = async () => {
    if (!selectedId) {
      showToast("Select an agent", "high");
      return;
    }

    // Reassign mode: task already has an agent, and the user wants
    // to swap. Skip the prepare/kickoff dance — the backend's
    // reassign_task clears working_path so the cycle preps a fresh
    // worktree for the new assignee on the next tick. Simpler flow,
    // no repo-path prompt needed.
    if (isReassign) {
      setAssigning(true);
      try {
        await api.reassignTask(task.id, {
          agent_id: selectedId,
          // plan_first is coding-only; the backend will ignore it for
          // other roles, but only send it when meaningful.
          plan_first: isCoding ? planFirst : undefined,
          custom_prompt: customPrompt || undefined,
        });
        const agent = profiles.find((p) => p.id === selectedId);
        showToast(`Reassigned to ${agent?.display_name || "agent"}`, "normal");
        onAssigned();
        onClose();
      } catch (err) {
        showToast(err.message || "Reassign failed", "high");
      }
      setAssigning(false);
      return;
    }

    // Fresh assign: coding agents still need a repo path the user
    // chooses. Review / investigation agents resolve the path from
    // config.
    if (expectedRole === "coding" && !repoPath) {
      showToast("Enter a repo path", "high");
      return;
    }
    if (!intent.trim()) {
      showToast("Tell the agent what you're trying to achieve (one sentence is enough)", "high");
      return;
    }
    setAssigning(true);
    try {
      // Persist the captured intent + boundaries onto task.metadata so
      // build_task_prompt picks them up for every future run of this
      // task (retry via cycle, resume, reassign).
      const intentTrimmed = intent.trim();
      const nonGoalsTrimmed = nonGoals.trim();
      const nextMeta = {
        ...(task.metadata || {}),
        description: intentTrimmed,
      };
      if (nonGoalsTrimmed) nextMeta.non_goals = nonGoalsTrimmed;
      else delete nextMeta.non_goals;
      if (intentTrimmed !== initialIntent || nonGoalsTrimmed !== (task.metadata?.non_goals || "")) {
        await api.updateTask(task.id, { metadata: nextMeta });
      }
      await api.assignAgent({
        task_id: task.id,
        profile_id: selectedId,
        repo_path: repoPath,
        plan_first: planFirst,
        custom_prompt: customPrompt || undefined,
        branch_name: branchName || undefined,
        specialty_id: specialtyId || undefined,
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
    <ModalPortal>
    <div className="modal-overlay" onClick={onClose}>
      <div className="assign-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <Bot size={16} />
          <span>{isReassign ? "Reassign Agent" : "Assign Agent"}</span>
          <span className="assign-task-title">{task.title}</span>
        </div>

        <div className="modal-body">
          {loading ? (
            <p className="assign-loading">Loading agents...</p>
          ) : (
            <>
              {profiles.length === 0 && (
                <div style={{ padding: "8px 12px", marginBottom: 8, background: "var(--pink-soft)", borderRadius: "var(--radius-xs)", fontSize: 12, color: "var(--pink)" }}>
                  No {roleMeta(expectedRole, agentTypes).label.toLowerCase()} agent for this task yet. Create one below.
                </div>
              )}
              <div className="assign-section-label">
                Available {roleMeta(expectedRole, agentTypes).label}s
              </div>
              <div className="assign-agent-list">
                {profiles.map((p) => {
                  const meta = roleMeta(p.role, agentTypes);
                  const RoleIcon = meta.icon;
                  return (
                    <div
                      key={p.id}
                      className={`assign-agent-option ${selectedId === p.id ? "selected" : ""}`}
                      onClick={() => setSelectedId(p.id)}
                    >
                      <span className="assign-avatar">
                        <CardAvatar agent={p} size="md" />
                      </span>
                      <div className="assign-agent-info">
                        <div className="assign-agent-name">
                          {p.display_name}
                          <span className="assign-agent-role">
                            <RoleIcon size={10} /> {meta.label}
                          </span>
                          {p.scope_repo && <span className="assign-agent-scope" title={p.scope_repo}>{formatRepo(p.scope_repo, defaultOrg)}</span>}
                          {!p.scope_repo && <span className="assign-agent-scope">global</span>}
                        </div>
                        {p.flavor_text && <div className="assign-agent-reasons">{p.flavor_text}</div>}
                      </div>
                    </div>
                  );
                })}
              </div>

              <button className="btn btn-sm" onClick={handleCreateNew} style={{ marginTop: 8 }}>
                <Plus size={10} /> Create a new {roleMeta(expectedRole, agentTypes).label.toLowerCase()}
              </button>

              {attachedSpecialties.length > 0 && !isReassign && (
                <>
                  <div className="assign-section-label" style={{ marginTop: 16 }}>
                    Use a specialty? <span style={{ fontWeight: 400, opacity: 0.7 }}>(optional)</span>
                  </div>
                  <select
                    style={{
                      width: "100%", padding: "8px 10px", fontSize: 12,
                      border: "1px solid var(--border)", borderRadius: "var(--radius-xs)",
                      background: "var(--bg)", color: "var(--text)", fontFamily: "var(--font)",
                    }}
                    value={specialtyId}
                    onChange={(e) => setSpecialtyId(e.target.value)}
                  >
                    <option value="">Base role only — no specialty</option>
                    {attachedSpecialties.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </>
              )}

              {!isReassign && (
                <>
                  <div className="assign-section-label" style={{ marginTop: 16 }}>
                    What are you trying to achieve?
                  </div>
                  <textarea
                    style={{
                      width: "100%", minHeight: 52, padding: "8px 10px", fontSize: 12,
                      border: "1px solid var(--border)", borderRadius: "var(--radius-xs)",
                      background: "var(--bg)", color: "var(--text)", fontFamily: "var(--font)",
                      resize: "vertical",
                    }}
                    value={intent}
                    onChange={(e) => setIntent(e.target.value)}
                    placeholder="One sentence is enough. What does done look like?"
                    autoFocus={!initialIntent}
                  />
                  <div className="assign-section-label" style={{ marginTop: 12 }}>
                    Anything the agent must not do? <span style={{ fontWeight: 400, opacity: 0.7 }}>(optional)</span>
                  </div>
                  <textarea
                    style={{
                      width: "100%", minHeight: 40, padding: "8px 10px", fontSize: 12,
                      border: "1px solid var(--border)", borderRadius: "var(--radius-xs)",
                      background: "var(--bg)", color: "var(--text)", fontFamily: "var(--font)",
                      resize: "vertical",
                    }}
                    value={nonGoals}
                    onChange={(e) => setNonGoals(e.target.value)}
                    placeholder="e.g. don't touch billing code, no new dependencies"
                  />
                </>
              )}

              {isCoding && !isReassign && (
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

              {!isCoding && !isReassign && (
                <div style={{ marginTop: 16, padding: "10px 12px", background: "var(--pink-soft)", borderRadius: "var(--radius-xs)", fontSize: 12, color: "var(--text-dim)" }}>
                  {roleMeta(expectedRole, agentTypes).label} runs autonomously — Maiko prepares a worktree and starts it in the background. You'll see the result in your inbox.
                </div>
              )}

              {isReassign && (
                <>
                  {isCoding && (
                    <>
                      <div className="assign-section-label" style={{ marginTop: 16 }}>Options</div>
                      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-dim)", cursor: "pointer" }}>
                        <input type="checkbox" checked={planFirst} onChange={(e) => setPlanFirst(e.target.checked)} />
                        Plan first
                        <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                          New agent proposes a plan for your approval before writing code
                        </span>
                      </label>
                    </>
                  )}

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
                    placeholder="e.g. Address the review comments from the previous run, but keep the existing test structure."
                  />

                  <div style={{ marginTop: 16, padding: "10px 12px", background: "var(--pink-soft)", borderRadius: "var(--radius-xs)", fontSize: 12, color: "var(--text-dim)" }}>
                    Reassigning drops the current agent's worktree and resets the task so the next cycle preps a fresh one for whomever you pick. The old commits aren't deleted (they're on the branch). Just the worktree checkout goes.
                  </div>
                </>
              )}
            </>
          )}
        </div>

        <div className="assign-footer">
          <button className="btn" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-primary"
            onClick={handleAssign}
            disabled={assigning || !selectedId || (isCoding && !isReassign && !repoPath) || selectedId === task.assigned_agent_id || (!isReassign && !intent.trim())}
          >
            <Rocket size={12} /> {assigning ? (isReassign ? "Reassigning..." : "Preparing...") : (isReassign ? "Reassign" : "Assign")}
          </button>
        </div>
      </div>
    </div>
    </ModalPortal>
  );
}
