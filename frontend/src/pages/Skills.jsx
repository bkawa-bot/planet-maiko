import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  Wand2, Sunrise, Brain, Coffee, Search, GitFork,
  Rocket, Clipboard, X, Loader, Plus, Save, Eye, Pencil, Trash2, Clock,
} from "lucide-react";
import "./Skills.css";

const ICON_MAP = {
  "sunrise": Sunrise, "brain": Brain, "coffee": Coffee,
  "search": Search, "git-fork": GitFork, "wand": Wand2,
};

export default function Skills() {
  const [skills, setSkills] = useState([]);
  const [selected, setSelected] = useState(null);
  const [editing, setEditing] = useState(false);
  const [editPrompt, setEditPrompt] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editMcps, setEditMcps] = useState("");
  const [editSchedule, setEditSchedule] = useState("");
  const [editCreatesPupdates, setEditCreatesPupdates] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newSkill, setNewSkill] = useState({ id: "", name: "", description: "", prompt: "", mcps: "", schedule_interval_minutes: "", creates_pupdates: false });

  const fetchSkills = () => api.getSkills().then(setSkills).catch(console.error);
  useEffect(() => { fetchSkills(); }, []);

  const openSkill = async (s) => {
    try {
      const detail = await api.getSkill(s.id);
      setSelected(detail);
      setEditPrompt(detail.prompt);
      setEditDesc(detail.description || "");
      setEditMcps((detail.mcps || []).join(", "));
      setEditSchedule(detail.schedule_interval_minutes ? String(detail.schedule_interval_minutes) : "");
      setEditCreatesPupdates(detail.creates_pupdates || false);
      setEditing(false);
      setResult(null);
    } catch (err) {
      setSelected(s);
    }
  };

  const saveEdit = async () => {
    await api.updateSkill(selected.id, {
      prompt: editPrompt,
      description: editDesc,
      mcps: editMcps.split(",").map(s => s.trim()).filter(Boolean),
      schedule_interval_minutes: editSchedule ? parseInt(editSchedule) : null,
      creates_pupdates: editCreatesPupdates,
    });
    showToast("Skill updated! ✏️", "normal");
    setEditing(false);
    fetchSkills();
    const updated = await api.getSkill(selected.id);
    setSelected(updated);
  };

  const runSkill = async (name) => {
    setRunning(true);
    setResult(null);
    showToast("Running skill... 🐕", "normal");
    try {
      const pupdates = await api.getPupdates();
      const tasks = await api.getTasks();
      const res = await api.runSkill(name, {
        context: {
          pupdates: JSON.stringify(pupdates.slice(0, 15)),
          tasks: JSON.stringify(tasks.slice(0, 15)),
          calendar: "[]", query: "", context: "",
        },
      });
      setResult(res);
      showToast(res.success ? "Skill complete! ✨" : "Skill had trouble", res.success ? "normal" : "high");
    } catch (err) {
      setResult({ success: false, error: err.message });
    }
    setRunning(false);
  };

  const handleCreate = async () => {
    if (!newSkill.id || !newSkill.name || !newSkill.prompt) {
      showToast("Need at least an ID, name, and prompt", "high");
      return;
    }
    try {
      await api.createSkill({
        ...newSkill,
        mcps: newSkill.mcps.split(",").map(s => s.trim()).filter(Boolean),
        schedule_interval_minutes: newSkill.schedule_interval_minutes ? parseInt(newSkill.schedule_interval_minutes) : null,
      });
      showToast(`Skill "${newSkill.name}" created! 🎉`, "normal");
      setShowCreate(false);
      setNewSkill({ id: "", name: "", description: "", prompt: "", mcps: "", schedule_interval_minutes: "", creates_pupdates: false });
      fetchSkills();
    } catch (err) {
      showToast(err.message || "Couldn't create skill", "high");
    }
  };

  const handleDelete = async (id) => {
    try {
      await api.deleteSkill(id);
      showToast("Skill deleted", "normal");
      setSelected(null);
      fetchSkills();
    } catch (err) {
      showToast(err.message || "Can't delete this skill", "high");
    }
  };

  const openCreate = () => {
    setNewSkill({ id: "", name: "", description: "", prompt: "# My Skill\n\nUse {pupdates} and {tasks} for context.\n\n## Instructions\n1. ...", mcps: "" });
    setShowCreate(true);
  };

  return (
    <div className="skills-page">
      <div className="skills-header">
        <Wand2 size={18} />
        <h2>Skills</h2>
        <button className="btn btn-primary" onClick={openCreate} style={{ marginLeft: "auto" }}>
          <Plus size={12} /> New Skill
        </button>
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="skill-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <Wand2 size={14} />
              <span>New Skill</span>
              <button className="btn btn-sm" onClick={() => setShowCreate(false)} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <div className="modal-body">
              <div className="skill-editor">
                <div className="skill-form-row">
                  <label>ID <input type="text" value={newSkill.id} onChange={e => setNewSkill(s => ({ ...s, id: e.target.value }))} placeholder="my-skill" /></label>
                  <label>Name <input type="text" value={newSkill.name} onChange={e => setNewSkill(s => ({ ...s, name: e.target.value }))} placeholder="My Custom Skill" /></label>
                </div>
                <label>Description <input type="text" value={newSkill.description} onChange={e => setNewSkill(s => ({ ...s, description: e.target.value }))} placeholder="What this skill does" /></label>
                <label>MCPs (comma-separated) <input type="text" value={newSkill.mcps} onChange={e => setNewSkill(s => ({ ...s, mcps: e.target.value }))} placeholder="slack, linear, figma" /></label>
                <div className="skill-form-row">
                  <label>Schedule
                    <select value={newSkill.schedule_interval_minutes} onChange={e => setNewSkill(s => ({ ...s, schedule_interval_minutes: e.target.value }))}>
                      <option value="">Manual only</option>
                      <option value="15">Every 15 min</option>
                      <option value="30">Every 30 min</option>
                      <option value="60">Every hour</option>
                      <option value="360">Every 6 hours</option>
                      <option value="720">Every 12 hours</option>
                      <option value="1440">Daily</option>
                    </select>
                  </label>
                  {newSkill.schedule_interval_minutes && (
                    <label className="checkbox-label">
                      <input type="checkbox" checked={newSkill.creates_pupdates} onChange={e => setNewSkill(s => ({ ...s, creates_pupdates: e.target.checked }))} />
                      Creates pupdates from output
                    </label>
                  )}
                </div>
                <label>Prompt
                  <textarea value={newSkill.prompt} onChange={e => setNewSkill(s => ({ ...s, prompt: e.target.value }))} rows={12} />
                </label>
                <div className="skill-form-actions">
                  <button className="btn" onClick={() => setShowCreate(false)}>Cancel</button>
                  <button className="btn btn-primary" onClick={handleCreate}><Plus size={12} /> Create Skill</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="skills-grid">
        {skills.map((s) => {
          const Icon = ICON_MAP[s.icon] || Wand2;
          return (
            <div key={s.id} className="skill-card card" onClick={() => openSkill(s)}>
              <Icon size={28} className="skill-icon" />
              <div className="skill-name">{s.name}</div>
              <div className="skill-desc">{s.description}</div>
              <div className="skill-mcps">
                {s.mcps?.map(m => <span key={m} className="tag">{m}</span>)}
                {s.schedule_interval_minutes && (
                  <span className="tag schedule-tag"><Clock size={8} /> {s.schedule_interval_minutes}m</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {selected && (
        <div className="modal-overlay" onClick={() => { setSelected(null); setResult(null); }}>
          <div className="skill-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <span>{selected.name}</span>
              {selected.is_default && <span className="badge">default</span>}
              <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                <button className="btn btn-sm" onClick={() => setEditing(!editing)}>
                  {editing ? <><Eye size={10} /> View</> : <><Pencil size={10} /> Edit</>}
                </button>
                {!selected.is_default && (
                  <button className="btn btn-sm btn-danger" onClick={() => handleDelete(selected.id)}>
                    <Trash2 size={10} />
                  </button>
                )}
                <button className="btn btn-sm" onClick={() => { setSelected(null); setResult(null); }}>
                  <X size={10} />
                </button>
              </div>
            </div>

            <div className="modal-body">
              {editing ? (
                <div className="skill-editor">
                  <label>Description
                    <input type="text" value={editDesc} onChange={e => setEditDesc(e.target.value)} />
                  </label>
                  <label>MCPs (comma-separated)
                    <input type="text" value={editMcps} onChange={e => setEditMcps(e.target.value)} />
                  </label>
                  <div className="skill-form-row">
                    <label>Schedule
                      <select value={editSchedule} onChange={e => setEditSchedule(e.target.value)}>
                        <option value="">Manual only</option>
                        <option value="15">Every 15 min</option>
                        <option value="30">Every 30 min</option>
                        <option value="60">Every hour</option>
                        <option value="360">Every 6 hours</option>
                        <option value="720">Every 12 hours</option>
                        <option value="1440">Daily</option>
                      </select>
                    </label>
                    {editSchedule && (
                      <label className="checkbox-label">
                        <input type="checkbox" checked={editCreatesPupdates} onChange={e => setEditCreatesPupdates(e.target.checked)} />
                        Creates pupdates from output
                      </label>
                    )}
                  </div>
                  <label>Prompt
                    <textarea value={editPrompt} onChange={e => setEditPrompt(e.target.value)} rows={15} />
                  </label>
                  <button className="btn btn-primary" onClick={saveEdit}><Save size={12} /> Save Changes</button>
                </div>
              ) : (
                <>
                  <p className="skill-modal-desc">{selected.description}</p>
                  {selected.mcps?.length > 0 && (
                    <div className="skill-modal-mcps">
                      MCPs: {selected.mcps.map(m => <span key={m} className="tag">{m}</span>)}
                    </div>
                  )}
                  <div className="skill-prompt-preview">
                    <div className="prompt-label">Prompt</div>
                    <pre className="prompt-text">{selected.prompt}</pre>
                  </div>
                  <div className="modal-actions">
                    <button className="btn btn-primary" onClick={() => runSkill(selected.id)} disabled={running}>
                      {running ? <><Loader size={12} className="spin" /> Running...</> : <><Rocket size={12} /> Run</>}
                    </button>
                    <button className="btn" onClick={() => { navigator.clipboard.writeText(selected.prompt); showToast("Prompt copied!", "normal"); }}>
                      <Clipboard size={12} /> Copy Prompt
                    </button>
                  </div>
                </>
              )}

              {result && (
                <div className={`skill-result ${result.success ? "" : "error"}`}>
                  {result.success ? (
                    <pre className="skill-output">{result.output}</pre>
                  ) : (
                    <div className="skill-error">{result.error}</div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
