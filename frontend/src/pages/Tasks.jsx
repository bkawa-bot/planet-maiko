import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import AssignAgentModal from "../components/AssignAgentModal";
import TaskCard from "../components/TaskCard";
import ViewPlanModal from "./tasks/ViewPlanModal";
import TaskDetailModal from "./tasks/TaskDetailModal";
import { formatRepo, useDefaultOrg, useConfiguredRepos } from "../utils/repo";
import {
  CheckSquare, Plus, FolderPlus, FolderOpen, ExternalLink,
  ChevronDown, ChevronRight, Folder, Loader,
  Play, X, Download, Sparkles, Trash2, Pencil, Brain,
} from "lucide-react";
import "./Tasks.css";
import "./cards.css";

export default function Tasks() {
  const defaultOrg = useDefaultOrg();
  const configuredRepos = useConfiguredRepos();
  const [tasks, setTasks] = useState([]);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showTaskForm, setShowTaskForm] = useState(false);
  const [showProjectForm, setShowProjectForm] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [collapsedGroups, setCollapsedGroups] = useState({});
  const [assigningTask, setAssigningTask] = useState(null);
  const [generatedTasks, setGeneratedTasks] = useState(null); // { project_id, tasks: [...] }
  const [generating, setGenerating] = useState(null); // project_id being generated
  const [reviseFeedback, setReviseFeedback] = useState(""); // free-form text for "Revise" button
  const [revising, setRevising] = useState(false); // true while LLM is rewriting the draft
  const [planning, setPlanning] = useState(null); // project_id being planned
  const [viewingPlan, setViewingPlan] = useState(null); // project object to view plan
  const [taskForm, setTaskForm] = useState({ title: "", description: "", type: "coding", priority: "normal", url: "", project_id: "", due_date: "" });
  const [projectForm, setProjectForm] = useState({ title: "", description: "", priority: "normal" });
  const [editingTask, setEditingTask] = useState(null);
  const [editForm, setEditForm] = useState({ title: "", description: "", type: "coding", priority: "normal", status: "new", project_id: "", url: "", due_date: "", repo: "" });
  const [detailTask, setDetailTask] = useState(null);

  const [config, setConfig] = useState(null);
  const [agentNames, setAgentNames] = useState({});
  const [profiles, setProfiles] = useState([]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [t, p, cfg, prof] = await Promise.all([api.getTasks(), api.getProjects(), api.getConfig(), api.getProfiles()]);
      setTasks(t);
      setProjects(p);
      setConfig(cfg);
      setProfiles(prof || []);
      setAgentNames(Object.fromEntries((prof || []).map((a) => [a.id, a.display_name])));
    } catch (err) { console.error(err); }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  const handleAction = async (e, id, action) => {
    e.stopPropagation();
    if (action === "start") await api.startTask(id);
    else if (action === "done") await api.completeTask(id);
    else if (action === "cancel") await api.cancelTask(id);
    else if (action === "launch") {
      try {
        await api.launchTask(id);
        showToast("On the way 🐾", "normal");
      } catch (err) {
        showToast("Couldn't launch: " + err.message, "high");
      }
    }
    fetchData();
  };

  const handleCreateTask = async (e) => {
    e.preventDefault();
    if (!taskForm.title.trim()) return;
    const { description, ...rest } = taskForm;
    const payload = { id: `task-${Date.now()}`, ...rest };
    if (description) payload.metadata = { description };
    await api.createTask(payload);
    setTaskForm({ title: "", description: "", type: "coding", priority: "normal", url: "", project_id: "", due_date: "" });
    setShowTaskForm(false);
    fetchData();
  };

  const handleCreateProject = async (e) => {
    e.preventDefault();
    if (!projectForm.title.trim()) return;
    await api.createProject({ id: `proj-${Date.now()}`, ...projectForm });
    setProjectForm({ title: "", description: "", priority: "normal" });
    setShowProjectForm(false);
    fetchData();
  };

  const toggleGroup = (id) => setCollapsedGroups((g) => ({ ...g, [id]: !g[id] }));

  // Group tasks by project
  const projectTasks = {};
  const ungrouped = [];
  for (const t of tasks) {
    if (t.project_id) {
      (projectTasks[t.project_id] = projectTasks[t.project_id] || []).push(t);
    } else {
      ungrouped.push(t);
    }
  }

  const activeTasks = tasks.filter((t) => t.status !== "done" && t.status !== "cancelled");

  if (loading) return <p className="page-empty">Loading...</p>;

  return (
    <div className="tasks-page frost-pane">
      {/* Create bar */}
      <div className="create-bar">
        <button className="btn" onClick={() => { setShowTaskForm(!showTaskForm); setShowProjectForm(false); }}>
          <Plus size={12} /> New Task
        </button>
        <button className="btn" onClick={() => { setShowProjectForm(!showProjectForm); setShowTaskForm(false); }}>
          <FolderPlus size={12} /> New Project
        </button>
        {config?.linear?.enabled && (
          <button className="btn" onClick={async () => {
            showToast("Importing from Linear...", "normal");
            try {
              const result = await api.importLinear();
              showToast(`${result.tasks_created} task(s) and ${result.projects_created} project(s) came in from Linear`, "normal");
              fetchData();
            } catch (err) {
              showToast(err.message || "Couldn't import from Linear", "high");
            }
          }}>
            <Download size={12} /> Import from Linear
          </button>
        )}
      </div>

      {/* Task creation modal */}
      {showTaskForm && (
        <div className="modal-overlay" onClick={() => setShowTaskForm(false)}>
          <div className="generated-tasks-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Plus size={14} />
              <span>New Task</span>
              <button className="btn btn-sm" onClick={() => setShowTaskForm(false)} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <form className="modal-body" onSubmit={handleCreateTask}>
              <div className="form-row">
                <label>
                  Title <span className="required">*</span>
                  <input type="text" value={taskForm.title} onChange={(e) => setTaskForm((f) => ({ ...f, title: e.target.value }))} autoFocus />
                </label>
              </div>
              <div className="form-row">
                <label>
                  Description
                  <textarea rows={3} value={taskForm.description} onChange={(e) => setTaskForm((f) => ({ ...f, description: e.target.value }))} placeholder="Optional details..." />
                </label>
              </div>
              <div className="form-row form-row-inline">
                <label>
                  Type
                  <select value={taskForm.type} onChange={(e) => setTaskForm((f) => ({ ...f, type: e.target.value }))}>
                    <option value="coding">Coding</option>
                    <option value="bug">Bug</option>
                    <option value="feature">Feature</option>
                    <option value="review">Review</option>
                    <option value="investigation">Investigation</option>
                    <option value="repo_analysis">Repo Analysis</option>
                    <option value="todo">Todo</option>
                    <option value="follow_up">Follow Up</option>
                  </select>
                </label>
                <label>
                  Priority
                  <select value={taskForm.priority} onChange={(e) => setTaskForm((f) => ({ ...f, priority: e.target.value }))}>
                    <option value="low">Low</option>
                    <option value="normal">Normal</option>
                    <option value="high">High</option>
                    <option value="urgent">Urgent</option>
                  </select>
                </label>
                <label>
                  Project
                  <select value={taskForm.project_id} onChange={(e) => setTaskForm((f) => ({ ...f, project_id: e.target.value }))}>
                    <option value="">None</option>
                    {projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
                  </select>
                </label>
              </div>
              <div className="form-row">
                <div className="form-row form-row-inline">
                  <label>URL <input type="text" value={taskForm.url} onChange={(e) => setTaskForm((f) => ({ ...f, url: e.target.value }))} placeholder="https://..." /></label>
                  <label>Due Date <input type="date" value={taskForm.due_date} onChange={(e) => setTaskForm((f) => ({ ...f, due_date: e.target.value }))} /></label>
                </div>
              </div>
              <div className="form-actions">
                <button type="button" className="btn" onClick={() => setShowTaskForm(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create Task</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Project creation modal */}
      {showProjectForm && (
        <div className="modal-overlay" onClick={() => setShowProjectForm(false)}>
          <div className="generated-tasks-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <FolderPlus size={14} />
              <span>New Project</span>
              <button className="btn btn-sm" onClick={() => setShowProjectForm(false)} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <form className="modal-body" onSubmit={handleCreateProject}>
              <div className="form-row">
                <label>Title <span className="required">*</span>
                  <input type="text" value={projectForm.title} onChange={(e) => setProjectForm((f) => ({ ...f, title: e.target.value }))} autoFocus />
                </label>
              </div>
              <div className="form-row">
                <label>Description <textarea value={projectForm.description} onChange={(e) => setProjectForm((f) => ({ ...f, description: e.target.value }))} rows={2} /></label>
              </div>
              <div className="form-actions">
                <button type="button" className="btn" onClick={() => setShowProjectForm(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Create Project</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {activeTasks.length === 0 && !showTaskForm && !showProjectForm ? (
        <div className="empty-state">
          <CheckSquare size={36} className="empty-icon" />
          <div className="empty-title">Nothing on your plate</div>
          <div className="empty-sub">Tasks come in from your pupdates, your agents, and projects you kick off. All quiet for now 🌱</div>
        </div>
      ) : (
        <>
          {/* Project groups — skip terminal-state projects. Backend
              cascades their tasks to cancelled when a project closes,
              but we also hide the group itself so a quick "I marked
              that project done" doesn't leave an empty card behind. */}
          {projects.filter((p) => p.status !== "done" && p.status !== "cancelled").map((project) => {
            const pts = projectTasks[project.id] || [];
            const done = pts.filter((t) => t.status === "done").length;
            const collapsed = collapsedGroups[project.id];
            const pct = pts.length > 0 ? Math.round((done / pts.length) * 100) : 0;

            return (
              <div key={project.id} className="project-group">
                <div className="project-group-header" onClick={() => toggleGroup(project.id)}>
                  {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  <Folder size={14} style={{ color: "var(--lavender)" }} />
                  <span className="project-group-title">{project.title}</span>
                  {project.source_id && <span className="project-group-id">{project.source_id}</span>}
                  <span className="project-progress-text">{done}/{pts.length}</span>
                  <div className="project-progress-bar">
                    <div className="project-progress-fill" style={{ width: `${pct}%` }} />
                  </div>
                  {project.description && project.description.length > 50 && (
                    <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); setViewingPlan(project); }} title="View project plan">
                      <FolderOpen size={10} /> Plan
                    </button>
                  )}
                  <button className="btn btn-sm btn-action" onClick={async (e) => {
                    e.stopPropagation();
                    setPlanning(project.id);
                    showToast("Maiko is creating a plan...", "normal");
                    try {
                      const result = await api.generatePlan(project.id);
                      showToast("Plan generated!", "normal");
                      fetchData();
                    } catch (err) {
                      showToast(err.message || "Couldn't generate plan", "high");
                    }
                    setPlanning(null);
                  }} disabled={planning === project.id}>
                    <Brain size={10} /> {planning === project.id ? "Planning..." : "Plan"}
                  </button>
                  <button className="btn btn-sm btn-action" onClick={async (e) => {
                    e.stopPropagation();
                    setGenerating(project.id);
                    showToast("Maiko is generating tasks...", "normal");
                    try {
                      const result = await api.generateTasks(project.id);
                      setGeneratedTasks(result);
                      showToast(`Generated ${result.tasks.length} task ideas!`, "normal");
                    } catch (err) {
                      showToast(err.message || "Couldn't generate tasks", "high");
                    }
                    setGenerating(null);
                  }} disabled={generating === project.id}>
                    <Sparkles size={10} /> {generating === project.id ? "..." : "Generate Tasks"}
                  </button>
                  {(project.metadata?.generated_tasks)?.length > 0 && (
                    <button className="btn btn-sm btn-approve" onClick={(e) => {
                      e.stopPropagation();
                      setGeneratedTasks({ project_id: project.id, tasks: project.metadata?.generated_tasks });
                    }}>
                      <Sparkles size={10} /> Review ({(project.metadata?.generated_tasks).length} ideas)
                    </button>
                  )}
                  {project.source_url && (
                    <a href={project.source_url} target="_blank" rel="noreferrer" className="btn btn-sm" onClick={(e) => e.stopPropagation()}>
                      <ExternalLink size={10} />
                    </a>
                  )}
                </div>
                {!collapsed && (pts.length > 0
                  ? pts.map((t) => (
                      <TaskCard
                        key={t.id}
                        task={t}
                        isExpanded={expanded === t.id}
                        onToggleExpand={() => setExpanded(expanded === t.id ? null : t.id)}
                        onAction={handleAction}
                        onAssignAgent={setAssigningTask}
                        onEdit={(task, form) => { setEditForm(form); setEditingTask(task); }}
                        onShowDetail={setDetailTask}
                        onRefresh={fetchData}
                        projects={projects}
                        agentNames={agentNames}
                      />
                    ))
                  : <div className="project-empty">No tasks yet. Create one and assign it to this project.</div>
                )}
              </div>
            );
          })}

          {/* Ungrouped tasks */}
          {ungrouped.filter((t) => t.status !== "done" && t.status !== "cancelled").map((t) => (
            <TaskCard
              key={t.id}
              task={t}
              isExpanded={expanded === t.id}
              onToggleExpand={() => setExpanded(expanded === t.id ? null : t.id)}
              onAction={handleAction}
              onAssignAgent={setAssigningTask}
              onEdit={(task, form) => { setEditForm(form); setEditingTask(task); }}
              onShowDetail={setDetailTask}
              onRefresh={fetchData}
              projects={projects}
              agentNames={agentNames}
            />
          ))}

        </>
      )}

      {assigningTask && (
        <AssignAgentModal
          task={assigningTask}
          onClose={() => setAssigningTask(null)}
          onAssigned={fetchData}
        />
      )}

      {/* Plan editor — review + edit generated tasks before approval */}
      {generatedTasks && (
        <div className="modal-overlay" onClick={() => setGeneratedTasks(null)}>
          <div className="plan-editor" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Sparkles size={14} />
              <span>Review Plan</span>
              <button className="btn btn-sm" onClick={() => setGeneratedTasks(null)} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <div className="modal-body">
              <p className="plan-editor-intro">
                Maiko drafted this plan. Tweak titles, deps, or agents inline. If
                the whole shape is off, ask Maiko to revise it (input at the
                bottom). When you're happy, Approve creates the tasks and routes
                them.
              </p>
              <div className="plan-task-list">
                {generatedTasks.tasks.map((gt, i) => (
                  <div key={i} className="plan-task card">
                    <div className="plan-task-header">
                      <span className="plan-task-idx">#{i + 1}</span>
                      <input
                        className="plan-task-title-input"
                        value={gt.title || ""}
                        onChange={(e) => setGeneratedTasks((p) => ({
                          ...p, tasks: p.tasks.map((t, j) => j === i ? { ...t, title: e.target.value } : t),
                        }))}
                      />
                      <button className="btn btn-sm btn-danger" title="Remove" onClick={() => setGeneratedTasks((p) => ({
                        ...p,
                        // Also drop this index from any task that depended on it, and shift indices > i down
                        tasks: p.tasks
                          .filter((_, j) => j !== i)
                          .map((t) => ({
                            ...t,
                            depends_on: (t.depends_on || [])
                              .filter((d) => d !== i)
                              .map((d) => d > i ? d - 1 : d),
                          })),
                      }))}><Trash2 size={9} /></button>
                    </div>
                    <div className="plan-task-row">
                      <select
                        className="plan-task-select"
                        value={gt.priority || "normal"}
                        onChange={(e) => setGeneratedTasks((p) => ({
                          ...p, tasks: p.tasks.map((t, j) => j === i ? { ...t, priority: e.target.value } : t),
                        }))}
                      >
                        {["urgent", "high", "normal", "low"].map((x) => <option key={x} value={x}>{x}</option>)}
                      </select>
                      <select
                        className="plan-task-select"
                        value={gt.type || "todo"}
                        onChange={(e) => setGeneratedTasks((p) => ({
                          ...p, tasks: p.tasks.map((t, j) => j === i ? { ...t, type: e.target.value } : t),
                        }))}
                      >
                        {["coding", "bug", "feature", "review", "investigation", "repo_analysis", "todo"].map((x) => <option key={x} value={x}>{x}</option>)}
                      </select>
                      <input
                        className="plan-task-input"
                        placeholder="repo (org/name)"
                        value={gt.repo || ""}
                        onChange={(e) => setGeneratedTasks((p) => ({
                          ...p, tasks: p.tasks.map((t, j) => j === i ? { ...t, repo: e.target.value } : t),
                        }))}
                      />
                      <input
                        className="plan-task-input"
                        placeholder="category"
                        value={gt.category || ""}
                        onChange={(e) => setGeneratedTasks((p) => ({
                          ...p, tasks: p.tasks.map((t, j) => j === i ? { ...t, category: e.target.value } : t),
                        }))}
                      />
                      {!["review", "investigation", "repo_analysis"].includes(gt.type || "todo") && (
                        <label
                          className="plan-task-plan-first"
                          title="Start the agent in plan mode — it produces a markdown plan first, waits for your approval, then implements. Good for bigger or fuzzier tasks."
                        >
                          <input
                            type="checkbox"
                            checked={!!gt.plan_first}
                            onChange={(e) => setGeneratedTasks((p) => ({
                              ...p, tasks: p.tasks.map((t, j) => j === i ? { ...t, plan_first: e.target.checked } : t),
                            }))}
                          />
                          plan first
                        </label>
                      )}
                    </div>
                    {gt.description && (
                      <div className="plan-task-desc">{gt.description}</div>
                    )}
                    <div className="plan-task-foot">
                      <div className="plan-task-deps">
                        <span className="plan-task-deps-label">Blocked by:</span>
                        {(gt.depends_on || []).length === 0 && <span className="plan-task-deps-none">nothing</span>}
                        {(gt.depends_on || []).map((d) => (
                          <span key={d} className="plan-dep-chip">
                            #{d + 1}
                            <button className="btn-ghost" onClick={() => setGeneratedTasks((p) => ({
                              ...p, tasks: p.tasks.map((t, j) => j === i ? { ...t, depends_on: (t.depends_on || []).filter((x) => x !== d) } : t),
                            }))}><X size={9} /></button>
                          </span>
                        ))}
                        <select
                          className="plan-dep-add"
                          value=""
                          onChange={(e) => {
                            const idx = parseInt(e.target.value, 10);
                            if (Number.isNaN(idx)) return;
                            setGeneratedTasks((p) => ({
                              ...p,
                              tasks: p.tasks.map((t, j) => j === i
                                ? { ...t, depends_on: [...new Set([...(t.depends_on || []), idx])].sort((a, b) => a - b) }
                                : t),
                            }));
                          }}
                        >
                          <option value="">+ add dep</option>
                          {generatedTasks.tasks.map((other, j) => (
                            j !== i && !(gt.depends_on || []).includes(j)
                              ? <option key={j} value={j}>#{j + 1}: {other.title?.slice(0, 40) || "(untitled)"}</option>
                              : null
                          ))}
                        </select>
                      </div>
                      <div className="plan-task-agent">
                        <span className="plan-task-deps-label">Agent:</span>
                        {(() => {
                          // Compatible agents = same role as the suggested
                          // role AND either same scope_repo or global. The
                          // user can also pick "spawn new" to let the
                          // backend lazy-spawn (matches the suggested
                          // chip's behavior when the suggested agent is
                          // marked spawn_new).
                          const role = gt.suggested_role || "coding";
                          const scope = gt.suggested_scope_repo || null;
                          const compatible = profiles.filter((p) => {
                            if (p.archived) return false;
                            if (p.role !== role) return false;
                            // Prefer exact scope match; allow global agents
                            // (no scope) as a fallback in the same dropdown.
                            if (scope && p.scope_repo && p.scope_repo !== scope) return false;
                            return true;
                          });
                          // Default selection: explicit override > suggested existing > spawn new
                          const currentValue = gt.assigned_agent_id
                            || (gt.suggested_agent?.spawn_new ? "__spawn__" : (gt.suggested_agent?.id || "__spawn__"));
                          return (
                            <select
                              className="plan-agent-select"
                              value={currentValue}
                              onChange={(e) => {
                                const v = e.target.value;
                                setGeneratedTasks((p) => ({
                                  ...p,
                                  tasks: p.tasks.map((t, j) => j === i
                                    ? { ...t, assigned_agent_id: v === "__spawn__" ? null : v }
                                    : t),
                                }));
                              }}
                              title={`Pick a ${role} agent for ${scope || "global"} scope`}
                            >
                              {compatible.map((p) => (
                                <option key={p.id} value={p.id}>
                                  {p.display_name}{p.scope_repo ? ` · ${formatRepo(p.scope_repo, defaultOrg)}` : " · global"}
                                </option>
                              ))}
                              <option value="__spawn__">
                                ✨ Spawn new {role} agent{scope ? ` for ${scope}` : ""}
                              </option>
                            </select>
                          );
                        })()}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="generated-tasks-revise">
              <div className="plan-revise-label">
                <Sparkles size={11} /> Ask Maiko to revise
              </div>
              <div className="plan-revise-row">
                <input
                  className="plan-revise-input"
                  placeholder="e.g. 'break step 2 into smaller tasks' · 'add a testing phase' · 'drop the ui work'"
                  value={reviseFeedback}
                  onChange={(e) => setReviseFeedback(e.target.value)}
                  disabled={revising}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && reviseFeedback.trim() && !revising) {
                      e.preventDefault();
                      e.currentTarget.blur();
                      document.getElementById("plan-revise-btn")?.click();
                    }
                  }}
                />
                <button
                  id="plan-revise-btn"
                  className="btn btn-primary"
                  disabled={revising || !reviseFeedback.trim()}
                  title="Send your edits and feedback back to Maiko for another pass"
                  onClick={async () => {
                    setRevising(true);
                    try {
                      const res = await api.reviseTasks(
                        generatedTasks.project_id,
                        reviseFeedback.trim(),
                        generatedTasks.tasks,
                      );
                      setGeneratedTasks({ project_id: res.project_id, tasks: res.tasks });
                      setReviseFeedback("");
                      showToast("Plan updated", "normal");
                    } catch (err) {
                      showToast("Couldn't revise: " + err.message, "high");
                    } finally {
                      setRevising(false);
                    }
                  }}
                >
                  {revising
                    ? <><Loader size={10} className="spin" /> Revising…</>
                    : <><Sparkles size={10} /> Revise</>}
                </button>
              </div>
            </div>
            <div className="generated-tasks-footer">
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{generatedTasks.tasks.length} task(s)</span>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn" onClick={() => setGeneratedTasks(null)} disabled={revising}>Cancel</button>
                <button className="btn btn-primary" disabled={revising} onClick={async () => {
                  try {
                    const res = await api.approveProjectPlan(generatedTasks.project_id, generatedTasks.tasks);
                    const n = res.tasks_created?.length || 0;
                    const kickoffs = res.kickoffs || [];
                    const launched = kickoffs.filter((k) => k.success).length;
                    const failed = kickoffs.filter((k) => !k.success);
                    let msg = `Approved plan — ${n} task(s) created, ${launched} agent(s) launched`;
                    if (failed.length > 0) {
                      msg += `. ${failed.length} failed to launch (use Launch button to retry)`;
                      console.warn("[approve-plan] kickoff failures:", failed);
                    }
                    showToast(msg, failed.length > 0 ? "high" : "normal");
                    setGeneratedTasks(null);
                    fetchData();
                  } catch (err) {
                    showToast("Couldn't approve: " + err.message, "high");
                  }
                }}>
                  <CheckSquare size={12} /> Approve Plan
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Edit task modal */}
      {editingTask && (
        <div className="modal-overlay" onClick={() => setEditingTask(null)}>
          <div className="generated-tasks-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Pencil size={14} />
              <span>Edit Task</span>
              <button className="btn btn-sm" onClick={() => setEditingTask(null)} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <form className="modal-body" onSubmit={async (e) => {
              e.preventDefault();
              const { description, repo, ...rest } = editForm;
              const payload = { ...rest };
              const existingMeta = editingTask.metadata || {};
              const trimmedRepo = (repo || "").trim();
              payload.metadata = {
                ...existingMeta,
                description: description || "",
                // `repo` drives agent routing + worktree resolution (see
                // orchestration.scope_for_task / resolve_repo_path), so
                // editing it here is the canonical way to attach/detach
                // a repo from a task. Empty string clears the key rather
                // than keeping a stale value in extra.
                ...(trimmedRepo ? { repo: trimmedRepo } : {}),
              };
              if (!trimmedRepo && existingMeta.repo) {
                delete payload.metadata.repo;
              }
              await api.updateTask(editingTask.id, payload);
              showToast("Saved 🌱", "normal");
              setEditingTask(null);
              fetchData();
            }}>
              <div className="form-row">
                <label>
                  Title <span className="required">*</span>
                  <input type="text" value={editForm.title} onChange={(e) => setEditForm((f) => ({ ...f, title: e.target.value }))} autoFocus />
                </label>
              </div>
              <div className="form-row">
                <label>
                  Description
                  <textarea rows={3} value={editForm.description} onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))} placeholder="Optional details..." />
                </label>
              </div>
              <div className="form-row form-row-inline">
                <label>
                  Status
                  <select value={editForm.status} onChange={(e) => setEditForm((f) => ({ ...f, status: e.target.value }))}>
                    <option value="new">New</option>
                    <option value="in_progress">In Progress</option>
                    <option value="waiting">Waiting</option>
                    <option value="review">Review</option>
                    <option value="done">Done</option>
                    <option value="cancelled">Cancelled</option>
                  </select>
                </label>
                <label>
                  Type
                  <select value={editForm.type} onChange={(e) => setEditForm((f) => ({ ...f, type: e.target.value }))}>
                    <option value="coding">Coding</option>
                    <option value="bug">Bug</option>
                    <option value="feature">Feature</option>
                    <option value="review">Review</option>
                    <option value="investigation">Investigation</option>
                    <option value="repo_analysis">Repo Analysis</option>
                    <option value="todo">Todo</option>
                    <option value="follow_up">Follow Up</option>
                  </select>
                </label>
                <label>
                  Priority
                  <select value={editForm.priority} onChange={(e) => setEditForm((f) => ({ ...f, priority: e.target.value }))}>
                    <option value="low">Low</option>
                    <option value="normal">Normal</option>
                    <option value="high">High</option>
                    <option value="urgent">Urgent</option>
                  </select>
                </label>
                <label>
                  Project
                  <select value={editForm.project_id} onChange={(e) => setEditForm((f) => ({ ...f, project_id: e.target.value }))}>
                    <option value="">None</option>
                    {projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
                  </select>
                </label>
              </div>
              <div className="form-row">
                <div className="form-row form-row-inline">
                  <label>
                    Repo
                    <input
                      type="text"
                      value={editForm.repo}
                      onChange={(e) => setEditForm((f) => ({ ...f, repo: e.target.value }))}
                      placeholder="org/repo (drives agent routing)"
                      list={configuredRepos.length ? "tasks-edit-repos" : undefined}
                    />
                    {configuredRepos.length > 0 && (
                      <datalist id="tasks-edit-repos">
                        {configuredRepos.map((r) => <option key={r} value={r} />)}
                      </datalist>
                    )}
                  </label>
                  <label>URL <input type="text" value={editForm.url} onChange={(e) => setEditForm((f) => ({ ...f, url: e.target.value }))} placeholder="https://..." /></label>
                  <label>Due Date <input type="date" value={editForm.due_date} onChange={(e) => setEditForm((f) => ({ ...f, due_date: e.target.value }))} /></label>
                </div>
              </div>
              <div className="form-actions">
                <button type="button" className="btn" onClick={() => setEditingTask(null)}>Cancel</button>
                <button type="submit" className="btn btn-primary">Save Changes</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <ViewPlanModal plan={viewingPlan} onClose={() => setViewingPlan(null)} />
      <TaskDetailModal task={detailTask} onClose={() => setDetailTask(null)} />
    </div>
  );
}
