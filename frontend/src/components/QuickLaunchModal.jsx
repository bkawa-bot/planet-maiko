import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { ChevronDown, ChevronRight, Rocket, X, Loader } from "@icons";
import { useConfiguredRepos } from "../utils/repo";
import CardAvatar from "./CardAvatar";
import ModalPortal from "./ModalPortal";
import "./QuickLaunchModal.css";

/**
 * Direct agent-job launcher. The user picks the agent themselves
 * (skipping the pack-router LLM hop), types a prompt, optionally
 * layers a skill, and we mint a Task + queued AgentJob in one shot.
 *
 * Triggered from the Home "Launch agent" button or Cmd/Ctrl+K
 * anywhere via the "open-launch-agent" window event.
 */
const RUNNABLE_KINDS = [
  { value: "coding", label: "Coding" },
  { value: "investigation", label: "Investigation" },
  { value: "review", label: "Review" },
  { value: "cartograph", label: "Cartograph" },
  { value: "repo_analysis", label: "Repo analysis" },
];

const PRIORITIES = [
  { value: "low", label: "Low" },
  { value: "normal", label: "Normal" },
  { value: "high", label: "High" },
  { value: "urgent", label: "Urgent" },
];

export default function QuickLaunchModal({ open, onClose }) {
  const navigate = useNavigate();
  const configuredRepos = useConfiguredRepos();
  const [profiles, setProfiles] = useState([]);
  const [specialties, setSpecialties] = useState([]);
  const [loading, setLoading] = useState(false);
  const [launching, setLaunching] = useState(false);

  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [specialtyId, setSpecialtyId] = useState("");
  const [taskType, setTaskType] = useState("coding");
  const [scopeRepo, setScopeRepo] = useState("");
  const [priority, setPriority] = useState("normal");
  const [advancedOpen, setAdvancedOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    Promise.all([
      api.getProfiles().catch(() => []),
      api.getSkills().catch(() => []),
    ]).then(([ps, skills]) => {
      const list = (Array.isArray(ps) ? ps : []).filter((p) => !p.archived);
      setProfiles(list);
      setSpecialties(Array.isArray(skills) ? skills : []);
      if (list.length > 0 && !selectedAgentId) {
        setSelectedAgentId(list[0].id);
      }
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // When the picked agent has a scope_repo, prefill the repo field so
  // the user doesn't have to retype the common case.
  useEffect(() => {
    if (!selectedAgentId) return;
    const profile = profiles.find((p) => p.id === selectedAgentId);
    if (profile?.scope_repo && !scopeRepo) {
      setScopeRepo(profile.scope_repo);
    }
  }, [selectedAgentId]); // eslint-disable-line react-hooks/exhaustive-deps

  const reset = () => {
    setTitle("");
    setDescription("");
    setSpecialtyId("");
    setScopeRepo("");
    setPriority("normal");
    setTaskType("coding");
    setAdvancedOpen(false);
  };

  const handleClose = () => {
    if (launching) return;
    reset();
    onClose?.();
  };

  const handleLaunch = async () => {
    if (launching) return;
    const t = title.trim();
    if (!selectedAgentId || !t) {
      showToast("Pick an agent and write a prompt.", "high");
      return;
    }
    setLaunching(true);
    try {
      const r = await api.quickLaunchAgentJob({
        agent_profile_id: selectedAgentId,
        title: t,
        description: description.trim(),
        task_type: taskType,
        scope_repo: scopeRepo.trim() || undefined,
        priority,
        specialty_id: specialtyId || undefined,
      });
      showToast(`Launched. ${r.job?.id ? "Opening the job page…" : ""}`, "normal");
      reset();
      onClose?.();
      if (r?.job?.id) navigate(`/jobs/${r.job.id}`);
    } catch (err) {
      showToast(err.message || "Launch failed", "high");
    } finally {
      setLaunching(false);
    }
  };

  if (!open) return null;

  return (
    <ModalPortal>
      <div className="modal-overlay" onClick={handleClose}>
        <div className="quick-launch-modal" onClick={(e) => e.stopPropagation()}>
          <div className="quick-launch-header">
            <span><Rocket size={14} /> Launch agent</span>
            <button className="quick-launch-close" onClick={handleClose} disabled={launching}>
              <X size={12} />
            </button>
          </div>

          {loading ? (
            <div className="quick-launch-loading"><Loader size={12} className="spin" /> Loading…</div>
          ) : (
            <div className="quick-launch-body">
              <label className="quick-launch-field">
                <span>Agent</span>
                <select
                  value={selectedAgentId}
                  onChange={(e) => setSelectedAgentId(e.target.value)}
                  disabled={profiles.length === 0}
                >
                  {profiles.length === 0 && <option value="">— no agents available —</option>}
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name}{p.role ? ` · ${p.role}` : ""}{p.scope_repo ? ` · ${p.scope_repo}` : ""}
                    </option>
                  ))}
                </select>
                {selectedAgentId && profiles.find((p) => p.id === selectedAgentId) && (
                  <div className="quick-launch-agent-preview">
                    <CardAvatar agent={profiles.find((p) => p.id === selectedAgentId)} size={28} />
                    <span className="quick-launch-agent-bio">
                      {profiles.find((p) => p.id === selectedAgentId)?.flavor_text || ""}
                    </span>
                  </div>
                )}
              </label>

              <label className="quick-launch-field">
                <span>Prompt</span>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="What should the agent do?"
                  autoFocus
                />
              </label>

              <label className="quick-launch-field">
                <span>Additional context <small>(optional)</small></span>
                <textarea
                  rows={3}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Anything that helps the agent: URLs, files to look at, constraints, etc."
                />
              </label>

              {specialties.length > 0 && (
                <label className="quick-launch-field">
                  <span>Run as a skill <small>(optional)</small></span>
                  <select
                    value={specialtyId}
                    onChange={(e) => setSpecialtyId(e.target.value)}
                  >
                    <option value="">— none, run as a regular task —</option>
                    {specialties.map((s) => (
                      <option key={s.id} value={s.id}>{s.name || s.id}</option>
                    ))}
                  </select>
                </label>
              )}

              <button
                className="quick-launch-advanced-toggle"
                type="button"
                onClick={() => setAdvancedOpen((v) => !v)}
              >
                {advancedOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                {" "}Advanced
              </button>

              {advancedOpen && (
                <div className="quick-launch-advanced">
                  <label className="quick-launch-field">
                    <span>Kind</span>
                    <select value={taskType} onChange={(e) => setTaskType(e.target.value)}>
                      {RUNNABLE_KINDS.map((k) => (
                        <option key={k.value} value={k.value}>{k.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="quick-launch-field">
                    <span>Repo <small>(org/repo)</small></span>
                    <input
                      type="text"
                      value={scopeRepo}
                      onChange={(e) => setScopeRepo(e.target.value)}
                      placeholder="org/repo"
                      list={configuredRepos.length ? "quick-launch-repos" : undefined}
                    />
                    {configuredRepos.length > 0 && (
                      <datalist id="quick-launch-repos">
                        {configuredRepos.map((r) => <option key={r} value={r} />)}
                      </datalist>
                    )}
                  </label>
                  <label className="quick-launch-field">
                    <span>Priority</span>
                    <select value={priority} onChange={(e) => setPriority(e.target.value)}>
                      {PRIORITIES.map((p) => (
                        <option key={p.value} value={p.value}>{p.label}</option>
                      ))}
                    </select>
                  </label>
                </div>
              )}

              <div className="quick-launch-actions">
                <button className="btn btn-sm" onClick={handleClose} disabled={launching}>
                  Cancel
                </button>
                <button
                  className="btn btn-sm btn-primary"
                  onClick={handleLaunch}
                  disabled={launching || !title.trim() || !selectedAgentId}
                >
                  {launching ? <><Loader size={10} className="spin" /> Launching…</> : <><Rocket size={10} /> Launch</>}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </ModalPortal>
  );
}
