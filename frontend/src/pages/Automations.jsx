import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  Zap, Wand2, Sunrise, Brain, Coffee, Search, GitFork,
  Rocket, Clipboard, X, Loader, Plus, Save, Eye, Pencil, Trash2,
  Compass, Pause, Play, ChevronDown, ChevronRight,
} from "@icons";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import { relativeTime } from "../utils/dates";
import AutomationEditor from "../components/AutomationEditor";
import ModalPortal from "../components/ModalPortal";
import "./Automations.css";

const ICON_MAP = {
  "sunrise": Sunrise, "brain": Brain, "coffee": Coffee,
  "search": Search, "git-fork": GitFork, "wand": Wand2,
};

// This page hosts two surfaces: the when/then Automations list at
// the top, and the Specialties list (role protocol prompts an agent
// adopts when doing a specific kind of work) below. Things that
// don't fit the specialty model are hidden from the grid:
//   - pr-review: invoked internally by the review agent
//   - agent-protocol: global CLAUDE.md template, not a role
//
// The API methods are still named api.getSkills / api.runSkill / etc.
// because the backend routes (/skills/*) haven't been renamed yet —
// that's a follow-up. Internal state, callbacks, and CSS classes here
// use "specialty" to match what the user sees on screen.
const HIDDEN_SPECIALTY_IDS = new Set([
  "pr-review",
  "agent-protocol",
]);

export default function Automations() {
  const [specialties, setSpecialties] = useState([]);
  const [selected, setSelected] = useState(null);
  const [editing, setEditing] = useState(false);
  const [editPrompt, setEditPrompt] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editMcps, setEditMcps] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newSpecialty, setNewSpecialty] = useState({ id: "", name: "", description: "", prompt: "", mcps: "", needs_worktree: false });
  const [activeTab, setActiveTab] = useState("automations");

  const fetchSpecialties = () => api.getSkills()
    .then((list) => setSpecialties(list.filter((s) => !HIDDEN_SPECIALTY_IDS.has(s.id))))
    .catch(console.error);
  useEffect(() => { fetchSpecialties(); }, []);

  const openSpecialty = async (s) => {
    try {
      const detail = await api.getSkill(s.id);
      setSelected(detail);
      setEditPrompt(detail.prompt);
      setEditDesc(detail.description || "");
      setEditMcps((detail.mcps || []).join(", "));
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
    });
    showToast("Specialty updated! ✏️", "normal");
    setEditing(false);
    fetchSpecialties();
    const updated = await api.getSkill(selected.id);
    setSelected(updated);
  };

  const runSpecialty = async (name) => {
    setRunning(true);
    setResult(null);
    showToast("Running specialty... 🐕", "normal");
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
      const queued = res.status === "queued";
      showToast(
        queued
          ? "Queued — the cycle will pick it up shortly 🐾"
          : res.success
            ? "Specialty run complete ✨"
            : "Specialty run had trouble",
        res.success ? "normal" : "high",
      );
    } catch (err) {
      setResult({ success: false, error: err.message });
    }
    setRunning(false);
  };

  const handleCreate = async () => {
    if (!newSpecialty.id || !newSpecialty.name || !newSpecialty.prompt) {
      showToast("Need at least an ID, name, and prompt", "high");
      return;
    }
    try {
      await api.createSkill({
        ...newSpecialty,
        mcps: newSpecialty.mcps.split(",").map(s => s.trim()).filter(Boolean),
      });
      showToast(`Specialty "${newSpecialty.name}" created! 🎉`, "normal");
      setShowCreate(false);
      setNewSpecialty({ id: "", name: "", description: "", prompt: "", mcps: "", needs_worktree: false });
      fetchSpecialties();
    } catch (err) {
      showToast(err.message || "Couldn't create specialty", "high");
    }
  };

  const handleDelete = async (id) => {
    try {
      await api.deleteSkill(id);
      showToast("Specialty deleted", "normal");
      setSelected(null);
      fetchSpecialties();
    } catch (err) {
      showToast(err.message || "Can't delete this specialty", "high");
    }
  };

  const openCreate = () => {
    setNewSpecialty({ id: "", name: "", description: "", prompt: "# My Specialty\n\nUse {pupdates} and {tasks} for context.\n\n## Instructions\n1. ...", mcps: "", needs_worktree: false });
    setShowCreate(true);
  };

  return (
    <div className="specialties-page frost-pane">
      <div className="page-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={activeTab === "automations"}
          className={`page-tab ${activeTab === "automations" ? "active" : ""}`}
          onClick={() => setActiveTab("automations")}
        >
          Automations
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "specialties"}
          className={`page-tab ${activeTab === "specialties" ? "active" : ""}`}
          onClick={() => setActiveTab("specialties")}
        >
          Specialties
        </button>
      </div>

      {activeTab === "automations" && <AutomationsList />}

      {activeTab === "specialties" && (
      <div className="skills-section-header">
        <h3>Specialties</h3>
        <p className="skills-section-sub">Role protocols agents adopt when doing a specific kind of work (analysis, triage, brainstorming). Run on-demand, on a cadence, or spawn a dedicated agent for a specialty from the Pack page. Running a specialty either uses an existing agent with that role or lazy-spawns one.</p>
        <button className="btn btn-primary" onClick={openCreate} style={{ marginLeft: "auto" }}>
          <Plus size={12} /> New Specialty
        </button>
      </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <ModalPortal>
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="specialty-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <Zap size={14} />
              <span>New Specialty</span>
              <button className="btn btn-sm" onClick={() => setShowCreate(false)} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <div className="modal-body">
              <div className="specialty-editor">
                <div className="specialty-form-row">
                  <label>ID <input type="text" value={newSpecialty.id} onChange={e => setNewSpecialty(s => ({ ...s, id: e.target.value }))} placeholder="error-triage" /></label>
                  <label>Name <input type="text" value={newSpecialty.name} onChange={e => setNewSpecialty(s => ({ ...s, name: e.target.value }))} placeholder="Error triage" /></label>
                </div>
                <label>Description <input type="text" value={newSpecialty.description} onChange={e => setNewSpecialty(s => ({ ...s, description: e.target.value }))} placeholder="What agents doing this specialty produce" /></label>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={!!newSpecialty.needs_worktree}
                    onChange={e => setNewSpecialty(s => ({ ...s, needs_worktree: e.target.checked }))}
                  />
                  Needs a repo worktree
                  <small style={{ display: "block", marginTop: "2px", fontStyle: "italic", color: "var(--text-muted)" }}>
                    On for specialties that read actual code (investigation-style). Off for ones that just compose a prompt from DB state (brainstorm / planning / analysis).
                  </small>
                </label>
                <label>MCPs (comma-separated) <input type="text" value={newSpecialty.mcps} onChange={e => setNewSpecialty(s => ({ ...s, mcps: e.target.value }))} placeholder="slack, linear, figma" /></label>
                <label>Prompt
                  <textarea value={newSpecialty.prompt} onChange={e => setNewSpecialty(s => ({ ...s, prompt: e.target.value }))} rows={12} />
                </label>
                <div className="specialty-form-actions">
                  <button className="btn" onClick={() => setShowCreate(false)}>Cancel</button>
                  <button className="btn btn-primary" onClick={handleCreate}><Plus size={12} /> Create Specialty</button>
                </div>
              </div>
            </div>
          </div>
        </div>
        </ModalPortal>
      )}

      {activeTab === "specialties" && (
      <div className="specialties-grid">
        {specialties.map((s) => {
          const Icon = ICON_MAP[s.icon] || Wand2;
          return (
            <div key={s.id} className="specialty-card card" onClick={() => openSpecialty(s)}>
              <Icon size={28} className="specialty-icon" />
              <div className="specialty-name">{s.name}</div>
              <div className="specialty-desc">{s.description}</div>
              <div className="specialty-mcps">
                {s.mcps?.map(m => <span key={m} className="tag">{m}</span>)}
              </div>
            </div>
          );
        })}
      </div>
      )}

      {selected && (
        <ModalPortal>
        <div className="modal-overlay" onClick={() => { setSelected(null); setResult(null); }}>
          <div className="specialty-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <span>{selected.name}</span>
              {selected.is_default && <span className="badge">default</span>}
              <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                <button className="btn btn-sm" onClick={() => setEditing(!editing)}>
                  {editing ? <><Eye size={10} /> View</> : <><Pencil size={10} /> Edit</>}
                </button>
                <button className="btn btn-sm btn-danger" onClick={() => handleDelete(selected.id)}>
                  <Trash2 size={10} />
                </button>
                <button className="btn btn-sm" onClick={() => { setSelected(null); setResult(null); }}>
                  <X size={10} />
                </button>
              </div>
            </div>

            <div className="modal-body">
              {editing ? (
                <div className="specialty-editor">
                  <label>Description
                    <input type="text" value={editDesc} onChange={e => setEditDesc(e.target.value)} />
                  </label>
                  <label>MCPs (comma-separated)
                    <input type="text" value={editMcps} onChange={e => setEditMcps(e.target.value)} />
                  </label>
                  <label>Prompt
                    <textarea value={editPrompt} onChange={e => setEditPrompt(e.target.value)} rows={15} />
                  </label>
                  <button className="btn btn-primary" onClick={saveEdit}><Save size={12} /> Save Changes</button>
                </div>
              ) : (
                <>
                  <p className="specialty-modal-desc">{selected.description}</p>
                  {selected.mcps?.length > 0 && (
                    <div className="specialty-modal-mcps">
                      MCPs: {selected.mcps.map(m => <span key={m} className="tag">{m}</span>)}
                    </div>
                  )}
                  <div className="specialty-prompt-preview">
                    <div className="prompt-label">Prompt</div>
                    <pre className="prompt-text">{selected.prompt}</pre>
                  </div>
                  <div className="modal-actions">
                    <button className="btn btn-primary" onClick={() => runSpecialty(selected.id)} disabled={running}>
                      {running ? <><Loader size={12} className="spin" /> Running...</> : <><Rocket size={12} /> Run</>}
                    </button>
                    <button className="btn" onClick={() => { navigator.clipboard.writeText(selected.prompt); showToast("Prompt copied!", "normal"); }}>
                      <Clipboard size={12} /> Copy Prompt
                    </button>
                  </div>
                </>
              )}

              {result && (
                <div className={`specialty-result ${result.success ? "" : "error"}`}>
                  {result.success ? (
                    <pre className="specialty-output">{result.output}</pre>
                  ) : (
                    <div className="specialty-error">{result.error}</div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
        </ModalPortal>
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
    case "run_agent_job": {
      const askNote = cfg.ask_first ? " (asks first)" : "";
      const kind = cfg.kind ? ` [${cfg.kind}]` : "";
      return `run agent job "${cfg.title || "(untitled)"}"${kind}${askNote}`;
    }
    case "create_task": {
      const type = cfg.type ? ` [${cfg.type}]` : "";
      return `create task "${cfg.title || "(untitled)"}"${type}`;
    }
    case "spawn_agent_job_from_pupdate": {
      const askNote = cfg.ask_first ? " (asks first)" : "";
      return `spawn ${cfg.kind || "agent job"} from pupdate${askNote}`;
    }
    case "create_task_from_pupdate": {
      const bits = [];
      if (cfg.task_type) bits.push(`type=${cfg.task_type}`);
      return `create task from pupdate${bits.length ? ` (${bits.join(", ")})` : ""}`;
    }
    case "dismiss_pupdate":
      return "dismiss the matched pupdate";
    case "complete_linked_task":
      return "close tasks linked to the matched PR";
    case "skip":
      return "skip (leave for manual)";
    // Legacy — keep readable until migration runs.
    case "propose":
      return `propose "${cfg.draft?.title || "(untitled)"}"`;
    case "run_skill":
      return `run skill "${cfg.skill_name || "(none)"}"`;
    case "nudge":
      return `reminder "${cfg.title || "(untitled)"}" (legacy)`;
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


