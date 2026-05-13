import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { Plus, Flame, Target, X } from "@icons";
import InfoButton from "../components/InfoButton";
import AgentsActiveTab from "../components/agents/AgentsActiveTab";
import AgentsProfilesTab from "../components/agents/AgentsProfilesTab";
import AgentsInsightsTab from "../components/agents/AgentsInsightsTab";
import ModalPortal from "../components/ModalPortal";
import { useConfiguredRepos } from "../utils/repo";
import "./Agents.css";

export default function Agents() {
  const configuredRepos = useConfiguredRepos();
  const [tab, setTab] = useState("active");
  const [profiles, setProfiles] = useState([]);
  const [agents, setAgents] = useState([]);
  const [activity, setActivity] = useState([]);
  const [queued, setQueued] = useState([]);
  const [conflicts, setConflicts] = useState([]);
  const [allLearnings, setAllLearnings] = useState({});
  const [loading, setLoading] = useState(true);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createForm, setCreateForm] = useState({ role: "coding", scope_repo: "", instructions: "", specialty_ids: [] });
  const [creating, setCreating] = useState(false);
  // Specialties the agent can be launched with. Role drives runtime
  // dispatch; specialty_ids is the attached-context pool a run picks
  // from. Fetched once on mount — the list changes rarely and the
  // Specialties tab is the canonical place to edit the set.
  const [specialties, setSpecialties] = useState([]);

  const fetchData = async () => {
    try {
      const [p, a, act, q, conf, learnings] = await Promise.all([
        api.getProfiles(),
        api.getAgents(),
        api.getAgentActivity(),
        api.getQueuedAgentTasks().catch(() => []),
        api.getConflicts().catch(() => []),
        api.getLearnings().catch(() => []),
      ]);
      setProfiles(p);
      setAgents(a);
      setActivity(act);
      setQueued(q);
      setConflicts(conf);
      setAllLearnings(Object.fromEntries(learnings.map((l) => [l.id, l])));
    } catch (err) {
      console.error(err);
    }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  useEffect(() => {
    // Pull the list of specialties (CustomSkills) so the role picker
    // can offer them alongside the built-in roles. Silent on failure —
    // the dropdown just doesn't show specialties if /skills errors.
    api.getSkills()
      .then((list) => setSpecialties(Array.isArray(list) ? list : []))
      .catch(() => setSpecialties([]));
  }, []);

  const handleCreateAgent = () => {
    // Open the pre-creation form so the user can pick role/scope first.
    // The arrival greeting follows once the profile is persisted.
    setCreateForm({ role: "coding", scope_repo: "", instructions: "", specialty_ids: [] });
    setShowCreateForm(true);
  };

  const toggleSpecialty = (id) => {
    setCreateForm((prev) => {
      const has = prev.specialty_ids.includes(id);
      return {
        ...prev,
        specialty_ids: has
          ? prev.specialty_ids.filter((x) => x !== id)
          : [...prev.specialty_ids, id],
      };
    });
  };

  const submitCreateAgent = async () => {
    setCreating(true);
    try {
      await api.createProfile({
        role: createForm.role,
        scope_repo: createForm.scope_repo.trim() || undefined,
        instructions: createForm.instructions.trim() || undefined,
        specialty_ids: createForm.specialty_ids,
      });
      setShowCreateForm(false);
      // Bio gen runs in a daemon thread server-side. The global
      // ArrivalWatcher (mounted at App level) polls and pops a full
      // arrival modal once the agent has self-named — replacing the
      // old fire-too-early modal that showed "Arriving…" as a name.
      showToast("A new agent is settling in… 🐾", "normal");
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
    <div className="agents-page frost-pane">
      {/* New Agent create form */}
      {showCreateForm && (
        <ModalPortal>
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
                    <option value="cartographer">Cartographer — maps repos into a playbook</option>
                  </select>
                </label>
                <label>
                  Scope (repo)
                  <input
                    type="text"
                    value={createForm.scope_repo}
                    onChange={(e) => setCreateForm({ ...createForm, scope_repo: e.target.value })}
                    placeholder="org/repo-name  (leave blank for global)"
                    list={configuredRepos.length ? "agents-create-repos" : undefined}
                  />
                  {configuredRepos.length > 0 && (
                    <datalist id="agents-create-repos">
                      {configuredRepos.map((r) => <option key={r} value={r} />)}
                    </datalist>
                  )}
                </label>
              </div>
              {specialties.length > 0 && (
                <div className="agent-edit-full">
                  {/* NOT a <label>: a label wrapping multiple buttons
                      auto-dispatches a click to the first one when the
                      label area is clicked, silently selecting the
                      first specialty without the user noticing. */}
                  <div className="agent-edit-label">Specialties (optional)</div>
                  <div className="agent-specialty-grid">
                    {specialties.map((s) => {
                      const checked = createForm.specialty_ids.includes(s.id);
                      return (
                        <button
                          type="button"
                          key={s.id}
                          className={`agent-specialty-chip ${checked ? "checked" : ""}`}
                          onClick={() => toggleSpecialty(s.id)}
                          title={s.description || s.name}
                        >
                          {s.name}
                        </button>
                      );
                    })}
                  </div>
                  <span className="agent-edit-hint">
                    Role drives how the agent runs. Specialties are extra context a run can layer on top, pick none, one, or many to attach.
                  </span>
                </div>
              )}
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
        </ModalPortal>
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
            <p>Each agent is a small character sheet — a name, an avatar, a role (coding, review, investigation, cartography), and a bio they wrote for themselves when they arrived. The bio rides along in every session, so the same agent feels like the same agent every time.</p>
            <h4>How they're picked for a task</h4>
            <p>When a task lands, Maiko looks for an agent matching (role, repo). First match wins; if nobody fits, a new pup arrives.</p>
            <h4>What they carry in</h4>
            <p>The team's playbook (active Insights from below), their own bio, the role's protocol, and an optional Specialty (a per-task playbook for things like triage or repo analysis). Agents don't carry personal learning sets — patterns from your team's PRs get retrieved on-the-fly when an agent reviews code.</p>
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
            <p>An end-of-day ritual: the pack gathers around the campfire and shares what they noticed today.</p>
            <h4>What agents share</h4>
            <ul>
              <li><strong>Feedback</strong> — coding rules that should apply to future work in a repo (goes to the Knowledge Pool, retrieved by future agents via <code>rules-relevant</code>).</li>
              <li><strong>Insights</strong> — tribal knowledge future agents should inherit, like tooling quirks or repo state (goes to the Pack Insights library → every agent's CLAUDE.md).</li>
            </ul>
            <h4>The flow</h4>
            <ol>
              <li><strong>Start the gathering</strong> — Maiko messages each active agent.</li>
              <li><strong>Watch speech bubbles appear</strong> as agents reply at the fire.</li>
              <li><strong>Wrap up</strong> when you're ready, then approve what sticks.</li>
              <li><strong>Finalize</strong> — learnings land in the pool, insights land in the library below.</li>
            </ol>
            <p>The library (collapsed below the ritual) holds the active playbook — the insights that currently get injected into every new agent's CLAUDE.md.</p>
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
          onRefresh={fetchData}
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
