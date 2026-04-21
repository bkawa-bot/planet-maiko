import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  Zap, Wand2, Sunrise, Brain, Coffee, Search, GitFork,
  Rocket, Clipboard, X, Loader, Plus, Save, Eye, Pencil, Trash2, Clock,
  Compass, Pause, Play, ChevronDown, ChevronRight,
} from "lucide-react";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import { relativeTime } from "../utils/dates";
import AutomationEditor from "../components/AutomationEditor";
import "./Automations.css";

const ICON_MAP = {
  "sunrise": Sunrise, "brain": Brain, "coffee": Coffee,
  "search": Search, "git-fork": GitFork, "wand": Wand2,
};

// Automations = the thinned-out Skills surface. Digest briefings live
// in Settings > Scheduled Briefings, investigation-class skills are
// now auto-spawned agent tasks, theme-designer is driven from the
// Theme menu, and pr-review is invoked internally by the review
// agent. agent-protocol stays visible here because it's the full
// coding-agent protocol template — an advanced knob power users may
// want to edit, distinct from Settings > Agent Preferences >
// role_instructions which only appends extra rules.
const HIDDEN_SKILL_IDS = new Set([
  "morning-brief", "brainstorm", "evening-wrap",
  "checkin", "plan", "team", "verify",
  "investigate", "repo-analysis",
  "theme-designer",
  "pr-review",
]);

export default function Automations() {
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

  const fetchSkills = () => api.getSkills()
    .then((list) => setSkills(list.filter((s) => !HIDDEN_SKILL_IDS.has(s.id))))
    .catch(console.error);
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
    showToast("Automation updated! ✏️", "normal");
    setEditing(false);
    fetchSkills();
    const updated = await api.getSkill(selected.id);
    setSelected(updated);
  };

  const runSkill = async (name) => {
    setRunning(true);
    setResult(null);
    showToast("Running automation... 🐕", "normal");
    try {
      const pupdates = await api.getPupdates();
      const tasks = await api.getTasks();
      // For repo-analysis, get the repo path from config
      let working_dir;
      if (name === "repo-analysis") {
        try {
          const cfg = await api.getConfig();
          const repos = cfg?.github?.repos || [];
          if (repos.length > 0) {
            // Prompt for which repo (use first as default)
            working_dir = prompt("Repo path to analyze:", repos[0]);
            if (!working_dir) { setRunning(false); return; }
          }
        } catch (e) {}
      }
      const res = await api.runSkill(name, {
        context: {
          pupdates: JSON.stringify(pupdates.slice(0, 15)),
          tasks: JSON.stringify(tasks.slice(0, 15)),
          calendar: "[]", query: "", context: "",
        },
        working_dir,
      });
      setResult(res);
      showToast(res.success ? "Automation complete! ✨" : "Automation had trouble", res.success ? "normal" : "high");
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
      showToast(`Automation "${newSkill.name}" created! 🎉`, "normal");
      setShowCreate(false);
      setNewSkill({ id: "", name: "", description: "", prompt: "", mcps: "", schedule_interval_minutes: "", creates_pupdates: false });
      fetchSkills();
    } catch (err) {
      showToast(err.message || "Couldn't create automation", "high");
    }
  };

  const handleDelete = async (id) => {
    try {
      await api.deleteSkill(id);
      showToast("Automation deleted", "normal");
      setSelected(null);
      fetchSkills();
    } catch (err) {
      showToast(err.message || "Can't delete this automation", "high");
    }
  };

  const openCreate = () => {
    setNewSkill({ id: "", name: "", description: "", prompt: "# My Automation\n\nUse {pupdates} and {tasks} for context.\n\n## Instructions\n1. ...", mcps: "" });
    setShowCreate(true);
  };

  return (
    <div className="skills-page frost-pane">
      <div className="skills-header">
        <Zap size={18} />
        <h2>Automations</h2>
      </div>

      <AutomationsList />

      <div className="skills-section-header">
        <h3>Skills</h3>
        <p className="skills-section-sub">Prompt templates that Automations can invoke. Also runnable on-demand or on a cadence of their own.</p>
        <button className="btn btn-primary" onClick={openCreate} style={{ marginLeft: "auto" }}>
          <Plus size={12} /> New Skill
        </button>
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="skill-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <Zap size={14} />
              <span>New Automation</span>
              <button className="btn btn-sm" onClick={() => setShowCreate(false)} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <div className="modal-body">
              <div className="skill-editor">
                <div className="skill-form-row">
                  <label>ID <input type="text" value={newSkill.id} onChange={e => setNewSkill(s => ({ ...s, id: e.target.value }))} placeholder="my-skill" /></label>
                  <label>Name <input type="text" value={newSkill.name} onChange={e => setNewSkill(s => ({ ...s, name: e.target.value }))} placeholder="My Custom Automation" /></label>
                </div>
                <label>Description <input type="text" value={newSkill.description} onChange={e => setNewSkill(s => ({ ...s, description: e.target.value }))} placeholder="What this automation does" /></label>
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
                  <button className="btn btn-primary" onClick={handleCreate}><Plus size={12} /> Create Automation</button>
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


// --------------------------------------------------------------------------
// AutomationsList — the new when/then dashboard. Sits above the Skills
// section. Each card is a single automation with trigger + action
// descriptions, status pill, pause/resume, and last-fired time.
// --------------------------------------------------------------------------

function AutomationsList() {
  const defaultOrg = useDefaultOrg();
  const [automations, setAutomations] = useState(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);  // {mode, automation?}
  const [defaultsOpen, setDefaultsOpen] = useState(false);

  const fetchAll = () => {
    setLoading(true);
    api.getAutomations()
      .then((rows) => setAutomations(rows || []))
      .catch(() => setAutomations([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchAll(); }, []);

  const toggle = async (a) => {
    const nextStatus = a.status === "active" ? "paused" : "active";
    setAutomations((rows) => rows.map((r) => r.id === a.id ? { ...r, status: nextStatus } : r));
    try {
      const updated = await api.updateAutomation(a.id, { status: nextStatus });
      setAutomations((rows) => rows.map((r) => r.id === a.id ? updated : r));
    } catch (err) {
      setAutomations((rows) => rows.map((r) => r.id === a.id ? { ...r, status: a.status } : r));
      showToast("Couldn't update automation: " + (err.message || "unknown"), "high");
    }
  };

  const yours = (automations || []).filter((a) => a.created_by !== "seed");
  const defaults = (automations || []).filter((a) => a.created_by === "seed");

  return (
    <div className="automations-section">
      <div className="skills-section-header">
        <h3><Compass size={14} style={{ verticalAlign: "middle" }} /> Automations</h3>
        <p className="skills-section-sub">
          When / then rules that tell Maiko what to do without asking you every time. Tap an automation to edit, pause to silence without deleting.
        </p>
        <button
          className="btn btn-sm btn-primary"
          style={{ marginLeft: "auto" }}
          onClick={() => setEditing({ mode: "create" })}
        >
          <Plus size={12} /> New automation
        </button>
      </div>

      {loading ? (
        <div className="automations-empty"><Loader size={12} className="spin" /> Loading…</div>
      ) : (
        <>
          <div className="automation-group-label">Yours</div>
          {yours.length === 0 ? (
            <div className="automations-empty">
              Nothing custom yet. Click "New automation" to build one, or approve a gap proposal from the inbox.
            </div>
          ) : (
            <div className="automations-list">
              {yours.map((a) => (
                <AutomationCard
                  key={a.id}
                  automation={a}
                  defaultOrg={defaultOrg}
                  onToggle={() => toggle(a)}
                  onEdit={() => setEditing({ mode: "edit", automation: a })}
                />
              ))}
            </div>
          )}

          {defaults.length > 0 && (
            <div className="automation-group-collapsible">
              <button
                className="automation-group-toggle"
                onClick={() => setDefaultsOpen((v) => !v)}
              >
                {defaultsOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <span>Defaults</span>
                <span className="automation-group-count">{defaults.length}</span>
                <span className="automation-group-note">built-ins that ship with Maiko — pause if one misfires</span>
              </button>
              {defaultsOpen && (
                <div className="automations-list">
                  {defaults.map((a) => (
                    <AutomationCard
                      key={a.id}
                      automation={a}
                      defaultOrg={defaultOrg}
                      onToggle={() => toggle(a)}
                      onEdit={() => setEditing({ mode: "edit", automation: a })}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {editing && (
        <AutomationEditor
          mode={editing.mode}
          automation={editing.automation}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); fetchAll(); }}
        />
      )}
    </div>
  );
}


function AutomationCard({ automation: a, defaultOrg, onToggle, onEdit }) {
  return (
    <div className={`automation-card card status-${a.status}`}>
      <button className="automation-card-click-target" onClick={onEdit} title="Edit this automation">
        <div className="automation-card-main">
          <div className="automation-card-name-row">
            <span className="automation-card-name">{a.name}</span>
          </div>
          {(a.scope_repo || a.status) && (
            <div className="automation-card-chips">
              <span className={`automation-card-status status-${a.status}`}>{a.status}</span>
              {a.execution_scope === "pupdate" && (
                <span className="automation-card-status" style={{ background: "var(--bg)", color: "var(--text-muted)" }}>rule</span>
              )}
              {a.scope_repo && (
                <span className="automation-card-repo" title={a.scope_repo}>
                  {formatRepo(a.scope_repo, defaultOrg)}
                </span>
              )}
            </div>
          )}
          {a.description && <div className="automation-card-desc">{a.description}</div>}
          <div className="automation-card-row">
            <span className="automation-card-label">WHEN</span>
            <span>{describeAutomationTrigger(a)}</span>
          </div>
          <div className="automation-card-row">
            <span className="automation-card-label">THEN</span>
            <span>{(a.then || []).map(describeAction).join(" → ") || "(no action)"}</span>
          </div>
          <div className="automation-card-footer">
            {a.last_fired_at ? (
              <>fired {relativeTime(a.last_fired_at)} · {a.fire_count || 0}× total</>
            ) : (
              <>never fired yet</>
            )}
            {a.cooldown_days > 0 && <> · {a.cooldown_days}d cooldown</>}
          </div>
        </div>
      </button>
      <div className="automation-card-actions">
        <button
          className="btn btn-sm"
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          disabled={a.status === "archived"}
          title={a.status === "active" ? "Pause this automation" : "Resume this automation"}
        >
          {a.status === "active" ? <Pause size={10} /> : <Play size={10} />}
          {a.status === "active" ? " pause" : a.status === "paused" ? " resume" : " archived"}
        </button>
      </div>
    </div>
  );
}


function describeCondition(trigger) {
  const cfg = trigger?.config || {};
  switch (trigger?.kind) {
    case "cadence": {
      if (cfg.interval_minutes) {
        const m = cfg.interval_minutes;
        if (m < 60) return `every ${m}min`;
        if (m % 60 === 0) return `every ${m / 60}h`;
        return `every ${Math.floor(m / 60)}h ${m % 60}min`;
      }
      return `every ${cfg.interval_hours || 24}h`;
    }
    case "overview_stale":
      return `overview >${cfg.stale_days || 30}d stale`;
    case "lora_missing":
      return `${cfg.min_learnings || 10}+ rules, no adapter`;
    case "pupdate_match": {
      const parts = [];
      if (cfg.source) parts.push(`source=${cfg.source}`);
      if (cfg.type) parts.push(`type=${cfg.type}`);
      if (cfg.types) parts.push(`type in [${cfg.types.join(", ")}]`);
      if (cfg.type_prefix) parts.push(`type starts with ${cfg.type_prefix}`);
      if (cfg.priority) parts.push(`priority=${cfg.priority}`);
      if (cfg.priority_in) parts.push(`priority in [${cfg.priority_in.join(", ")}]`);
      if (cfg.actionable !== undefined) parts.push(`actionable=${cfg.actionable}`);
      if (cfg.has_tag) parts.push(`tagged ${cfg.has_tag}`);
      if (cfg.title_contains) parts.push(`title contains "${cfg.title_contains}"`);
      if (cfg.within_minutes) parts.push(`within ${cfg.within_minutes}min`);
      return parts.length ? `pupdate where ${parts.join(" AND ")}` : "any pupdate";
    }
    case "pupdate_chain": {
      const types = (cfg.types || []).join(" + ");
      return `${types} within ${cfg.within_minutes || 30}min (same ${cfg.group_by || "repo"})`;
    }
    default:
      return trigger?.kind || "unknown";
  }
}

function describeAction(action) {
  const cfg = action?.config || {};
  switch (action?.kind) {
    case "propose":
      return `propose "${cfg.draft?.title || "(untitled)"}"`;
    case "nudge":
      return `nudge "${cfg.title || "(untitled)"}"`;
    case "create_task":
      return `create task "${cfg.title || "(untitled)"}"`;
    case "run_skill":
      return `run skill "${cfg.skill_name || "(none)"}"`;
    case "dismiss_pupdate":
      return "dismiss the matched pupdate";
    case "create_task_from_pupdate": {
      const bits = [];
      if (cfg.task_type) bits.push(`type=${cfg.task_type}`);
      if (cfg.task_priority) bits.push(`priority=${cfg.task_priority}`);
      return `create task from pupdate${bits.length ? ` (${bits.join(", ")})` : ""}`;
    }
    case "complete_linked_task":
      return "close tasks linked to the matched PR";
    case "skip":
      return "skip (leave for manual)";
    default:
      return action?.kind || "unknown";
  }
}

function describeAutomationTrigger(automation) {
  const when = automation.when || [];
  if (when.length === 0) return "no trigger";
  if (when.length === 1) return describeCondition(when[0]);
  const logic = automation.when_logic === "any" ? " OR " : " AND ";
  return when.map(describeCondition).join(logic);
}


