import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  Bot, Plus, Flame, Target, AlertTriangle, Code2, Eye, Search, X,
} from "lucide-react";
import InfoButton from "../components/InfoButton";
import AgentsActiveTab from "../components/agents/AgentsActiveTab";
import AgentsProfilesTab from "../components/agents/AgentsProfilesTab";
import AgentsInsightsTab from "../components/agents/AgentsInsightsTab";
import "./Agents.css";

const RANK_LABELS = { pup: "🌱 Pup", junior: "⭐ Junior", senior: "🌟 Senior", expert: "👑 Expert" };

export default function Agents() {
  const [tab, setTab] = useState("active");
  const [profiles, setProfiles] = useState([]);
  const [agents, setAgents] = useState([]);
  const [activity, setActivity] = useState([]);
  const [conflicts, setConflicts] = useState([]);
  const [allLearnings, setAllLearnings] = useState({});
  const [loading, setLoading] = useState(true);
  const [showArrival, setShowArrival] = useState(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createForm, setCreateForm] = useState({ role: "coding", scope_repo: "", instructions: "" });
  const [creating, setCreating] = useState(false);

  const fetchData = async () => {
    try {
      const [p, a, act, conf, learnings] = await Promise.all([
        api.getProfiles(),
        api.getAgents(),
        api.getAgentActivity(),
        api.getConflicts().catch(() => []),
        api.getLearnings().catch(() => []),
      ]);
      setProfiles(p);
      setAgents(a);
      setActivity(act);
      setConflicts(conf);
      setAllLearnings(Object.fromEntries(learnings.map((l) => [l.id, l])));
    } catch (err) {
      console.error(err);
    }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  const handleCreateAgent = () => {
    // Open the pre-creation form so the user can pick role/scope first.
    // The arrival greeting follows once the profile is persisted.
    setCreateForm({ role: "coding", scope_repo: "", instructions: "" });
    setShowCreateForm(true);
  };

  const submitCreateAgent = async () => {
    setCreating(true);
    try {
      const profile = await api.createProfile({
        role: createForm.role,
        scope_repo: createForm.scope_repo.trim() || undefined,
        instructions: createForm.instructions.trim() || undefined,
      });
      setShowCreateForm(false);
      setShowArrival(profile);
      showToast(`${profile.display_name} just arrived in town! 🐾`, "normal");
      fetchData();
    } catch (err) {
      showToast(err.message || "Create failed", "high");
    }
    setCreating(false);
  };

  const handleShowArchived = async (showArchived) => {
    const p = await api.getProfiles(showArchived ? { archived: "true" } : {});
    setProfiles(p);
  };

  if (loading) return <p className="page-empty">Loading...</p>;

  return (
    <div className="agents-page">
      {/* New Agent create form */}
      {showCreateForm && (
        <div className="modal-overlay" onClick={() => !creating && setShowCreateForm(false)}>
          <div className="agent-edit-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Plus size={14} /> New Agent
              <span style={{ flex: 1 }} />
              <button className="btn btn-sm" onClick={() => setShowCreateForm(false)} disabled={creating}>
                <X size={12} />
              </button>
            </div>
            <div className="modal-body agent-edit-body">
              <div className="agent-edit-row">
                <label>
                  Role
                  <select
                    value={createForm.role}
                    onChange={(e) => setCreateForm({ ...createForm, role: e.target.value })}
                  >
                    <option value="coding">Coder — writes code, opens PRs</option>
                    <option value="review">Reviewer — reviews PRs</option>
                    <option value="investigation">Investigator — digs into incidents & CI</option>
                  </select>
                </label>
                <label>
                  Scope (repo)
                  <input
                    type="text"
                    value={createForm.scope_repo}
                    onChange={(e) => setCreateForm({ ...createForm, scope_repo: e.target.value })}
                    placeholder="org/repo-name  (leave blank for global)"
                  />
                </label>
              </div>
              <label className="agent-edit-full">
                Starter instructions (optional, markdown)
                <textarea
                  rows={8}
                  value={createForm.instructions}
                  onChange={(e) => setCreateForm({ ...createForm, instructions: e.target.value })}
                  placeholder={"How should this agent work? You can also add this later via Edit.\n\nExample:\n## Your focus\n- Only touch files under src/auth/\n- Prefer tests before implementation\n- Ask before adding new dependencies"}
                />
              </label>
            </div>
            <div className="agent-edit-footer">
              <button className="btn" onClick={() => setShowCreateForm(false)} disabled={creating}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={submitCreateAgent} disabled={creating}>
                {creating ? "Spawning..." : <><Plus size={12} /> Create</>}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Arrival Modal */}
      {showArrival && (
        <div className="modal-overlay" onClick={() => setShowArrival(null)}>
          <div className="modal arrival-modal" onClick={(e) => e.stopPropagation()}>
            <div className="arrival-content">
              <div className="arrival-avatar"><Bot size={32} /></div>
              <h2 className="arrival-greeting">{showArrival.display_name}</h2>
              <p className="arrival-flavor">{showArrival.flavor_text}</p>
              <div className="arrival-rank">{RANK_LABELS[showArrival.rank] || "🌱 Pup"}</div>
              <div className="arrival-role">
                {(showArrival.role || "coding") === "coding" && <><Code2 size={10} /> Coder</>}
                {showArrival.role === "review" && <><Eye size={10} /> Reviewer</>}
                {showArrival.role === "investigation" && <><Search size={10} /> Investigator</>}
                {showArrival.scope_repo && <span className="arrival-scope"> · {showArrival.scope_repo}</span>}
                {!showArrival.scope_repo && <span className="arrival-scope"> · global</span>}
              </div>
              <button className="btn btn-primary" onClick={() => setShowArrival(null)}>
                Let's go!
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="inbox-tab-bar">
        <button
          className={`inbox-tab ${tab === "active" ? "active" : ""}`}
          onClick={() => setTab("active")}
        >
          Active
        </button>

        <button
          className={`inbox-tab ${tab === "profiles" ? "active" : ""}`}
          onClick={() => setTab("profiles")}
        >
          <Target size={10} /> Profiles
        </button>
        {tab === "profiles" && (
          <InfoButton title={<><Target size={16} /> Agent Profiles</>}>
            <p>Each agent is a <em>context set</em> — a personalized selection of learnings tuned for specific repos and task types.</p>
            <h4>Strengths</h4>
            <p>Categories where the agent scores above 70%. When an agent is strong in a category, those learnings get deprioritized in its brief — it's already mastered them, so the brief focuses on other areas.</p>
            <h4>Pup vs Senior</h4>
            <p>New agents ("pups") explore with random learnings and get an exploration bonus in recommendations. As they complete tasks, they specialize and rank up. Seniors exploit their proven learning sets.</p>
          </InfoButton>
        )}

        <button
          className={`inbox-tab ${tab === "insights" ? "active" : ""}`}
          onClick={() => setTab("insights")}
        >
          <Flame size={10} /> Pack Insights
        </button>
        {tab === "insights" && (
          <InfoButton title={<><Flame size={16} /> Pack Insights</>}>
            <p>A collaborative session where you and your agents share what you've learned.</p>
            <h4>The pipeline</h4>
            <ol>
              <li><strong>Start</strong> — signals agents to report their discoveries from recent work.</li>
              <li><strong>Collect</strong> — gathers feedback from agents, plus anything you add manually.</li>
              <li><strong>Synthesize</strong> — Maiko deduplicates, identifies what's already known, and proposes new rules.</li>
              <li><strong>Finalize</strong> — approved learnings merge into the Knowledge Pool and get used in future agent briefs.</li>
            </ol>
            <h4>When to use it</h4>
            <p>Run it after a productive session, at end of day, or whenever agents have been working on tasks. It's how the system gets smarter over time.</p>
          </InfoButton>
        )}

        <button className="btn btn-primary new-agent-btn" onClick={handleCreateAgent}>
          <Plus size={12} /> New Agent
        </button>
      </div>

      {tab === "active" && (
        <AgentsActiveTab
          agents={agents}
          activity={activity}
          conflicts={conflicts}
          profiles={profiles}
        />
      )}

      {tab === "profiles" && (
        <AgentsProfilesTab
          profiles={profiles}
          allLearnings={allLearnings}
          onCreateAgent={handleCreateAgent}
          onProfilesChanged={fetchData}
          onShowArchived={handleShowArchived}
        />
      )}

      {tab === "insights" && <AgentsInsightsTab />}
    </div>
  );
}
