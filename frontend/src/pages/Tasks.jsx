import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import AssignAgentModal from "../components/AssignAgentModal";
import TaskCard from "../components/TaskCard";
import {
  CheckSquare, Plus, FolderPlus, FolderOpen, ExternalLink,
  ChevronDown, ChevronRight, Folder,
  Play, X, Download, Sparkles, Trash2, Pencil, Brain,
} from "lucide-react";
import "./Tasks.css";
import "./Inbox.css";

export default function Tasks() {
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
  const [planning, setPlanning] = useState(null); // project_id being planned
  const [viewingPlan, setViewingPlan] = useState(null); // project object to view plan
  const [taskForm, setTaskForm] = useState({ title: "", description: "", type: "todo", priority: "normal", url: "", project_id: "", due_date: "" });
  const [projectForm, setProjectForm] = useState({ title: "", description: "", priority: "normal" });
  const [editingTask, setEditingTask] = useState(null);
  const [editForm, setEditForm] = useState({ title: "", description: "", type: "todo", priority: "normal", status: "new", project_id: "", url: "", due_date: "" });
  const [askingMaiko, setAskingMaiko] = useState(null);
  const [maikoQuery, setMaikoQuery] = useState("");
  const [maikoResult, setMaikoResult] = useState(null);
  const [maikoRunning, setMaikoRunning] = useState(false);
  const [detailTask, setDetailTask] = useState(null);

  const [config, setConfig] = useState(null);
  const [agentNames, setAgentNames] = useState({});

  const fetchData = async () => {
    setLoading(true);
    try {
      const [t, p, cfg, profiles] = await Promise.all([api.getTasks(), api.getProjects(), api.getConfig(), api.getProfiles()]);
      setTasks(t);
      setProjects(p);
      setConfig(cfg);
      setAgentNames(Object.fromEntries(profiles.map((a) => [a.id, a.display_name])));
    } catch (err) { console.error(err); }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  const handleAction = async (e, id, action) => {
    e.stopPropagation();
    if (action === "start") await api.startTask(id);
    else if (action === "done") await api.completeTask(id);
    else if (action === "cancel") await api.cancelTask(id);
    fetchData();
  };

  const handleCreateTask = async (e) => {
    e.preventDefault();
    if (!taskForm.title.trim()) return;
    const { description, ...rest } = taskForm;
    const payload = { id: `task-${Date.now()}`, ...rest };
    if (description) payload.metadata = { description };
    await api.createTask(payload);
    setTaskForm({ title: "", description: "", type: "todo", priority: "normal", url: "", project_id: "", due_date: "" });
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
    <div className="tasks-page">
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
              showToast(`Imported ${result.tasks_created} task(s), ${result.projects_created} project(s)`, "normal");
              fetchData();
            } catch (err) {
              showToast(err.message || "Import failed", "high");
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
                    <option value="todo">Todo</option>
                    <option value="pr_review">PR Review</option>
                    <option value="investigation">Investigation</option>
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
          <div className="empty-title">No active tasks</div>
          <div className="empty-sub">Tasks are created from pupdates, suggestions, and projects</div>
        </div>
      ) : (
        <>
          {/* Project groups */}
          {projects.map((project) => {
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
                  {(project.metadata?.generated_tasks || project.extra?.generated_tasks)?.length > 0 && (
                    <button className="btn btn-sm btn-approve" onClick={(e) => {
                      e.stopPropagation();
                      setGeneratedTasks({ project_id: project.id, tasks: project.metadata?.generated_tasks || project.extra?.generated_tasks });
                    }}>
                      <Sparkles size={10} /> Review ({(project.metadata?.generated_tasks || project.extra?.generated_tasks).length} ideas)
                    </button>
                  )}
                  <button className="btn btn-sm btn-action" onClick={(e) => {
                    e.stopPropagation();
                    setAskingMaiko({ id: project.id, title: project.title, type: "project", status: "active", project_id: project.id });
                  }}>
                    <Brain size={10} /> Ask Maiko
                  </button>
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
                        onAskMaiko={setAskingMaiko}
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
              onAskMaiko={setAskingMaiko}
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

      {/* Generated tasks review modal */}
      {generatedTasks && (
        <div className="modal-overlay" onClick={() => setGeneratedTasks(null)}>
          <div className="generated-tasks-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Sparkles size={14} />
              <span>Review Generated Tasks</span>
              <button className="btn btn-sm" onClick={() => setGeneratedTasks(null)} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <div className="modal-body">
              <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12 }}>
                Maiko suggested these tasks. Remove any you don't want, then approve.
              </p>
              <div className="generated-task-list">
                {generatedTasks.tasks.map((gt, i) => (
                  <div key={i} className="generated-task-item card">
                    <div className="generated-task-top">
                      <span className={`badge ${gt.priority}`}>{gt.priority}</span>
                      <span className="generated-task-title">{gt.title}</span>
                      <button className="btn btn-sm btn-danger" onClick={() => {
                        setGeneratedTasks((prev) => ({
                          ...prev,
                          tasks: prev.tasks.filter((_, j) => j !== i),
                        }));
                      }}><Trash2 size={9} /></button>
                    </div>
                    {gt.description && <div className="generated-task-desc">{gt.description}</div>}
                  </div>
                ))}
              </div>
            </div>
            <div className="generated-tasks-footer">
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{generatedTasks.tasks.length} task(s)</span>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn" onClick={() => setGeneratedTasks(null)}>Cancel</button>
                <button className="btn btn-primary" onClick={async () => {
                  for (let idx = 0; idx < generatedTasks.tasks.length; idx++) {
                    const gt = generatedTasks.tasks[idx];
                    await api.createTask({
                      id: `task-${Date.now()}-${String(idx).padStart(3, "0")}`,
                      title: gt.title,
                      type: gt.type || "todo",
                      priority: gt.priority || "normal",
                      project_id: generatedTasks.project_id,
                      metadata: gt.description ? { description: gt.description } : undefined,
                    });
                  }
                  // Clear generated tasks from project metadata
                  try {
                    await api.updateProject(generatedTasks.project_id, { metadata: { generated_tasks: null } });
                  } catch (e) {}
                  showToast(`Created ${generatedTasks.tasks.length} task(s)!`, "normal");
                  setGeneratedTasks(null);
                  fetchData();
                }}>
                  <CheckSquare size={12} /> Approve & Create All
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
              const { description, ...rest } = editForm;
              const payload = { ...rest };
              const existingMeta = editingTask.extra || editingTask.metadata || {};
              payload.metadata = { ...existingMeta, description: description || "" };
              await api.updateTask(editingTask.id, payload);
              showToast("Task updated", "normal");
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
                    <option value="todo">Todo</option>
                    <option value="pr_review">PR Review</option>
                    <option value="investigation">Investigation</option>
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

      {/* Ask Maiko modal */}
      {askingMaiko && (
        <div className="modal-overlay" onClick={() => { setAskingMaiko(null); setMaikoQuery(""); setMaikoResult(null); }}>
          <div className="generated-tasks-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Brain size={14} />
              <span>Ask Maiko about: {askingMaiko.title}</span>
              <button className="btn btn-sm" onClick={() => { setAskingMaiko(null); setMaikoQuery(""); setMaikoResult(null); }} style={{ marginLeft: "auto" }}><X size={10} /></button>
            </div>
            <div className="modal-body">
              <div className="form-row">
                <label>
                  What would you like Maiko to investigate?
                  <input
                    type="text"
                    value={maikoQuery}
                    onChange={(e) => setMaikoQuery(e.target.value)}
                    placeholder="e.g. What's the best approach for this?"
                    autoFocus
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && maikoQuery.trim() && !maikoRunning) {
                        e.preventDefault();
                        (async () => {
                          setMaikoRunning(true);
                          setMaikoResult(null);
                          try {
                            const res = await api.runSkill("investigate", {
                              context: {
                                query: maikoQuery,
                                context: `Task: ${askingMaiko.title}\nType: ${askingMaiko.type}\nStatus: ${askingMaiko.status}\nProject: ${askingMaiko.project_id || "none"}`,
                                pupdates: "[]", tasks: "[]", calendar: "[]",
                              },
                            });
                            setMaikoResult(res);
                          } catch (err) {
                            setMaikoResult({ error: err.message || "Something went wrong" });
                          }
                          setMaikoRunning(false);
                        })();
                      }
                    }}
                  />
                </label>
              </div>
              <div className="form-actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={!maikoQuery.trim() || maikoRunning}
                  onClick={async () => {
                    setMaikoRunning(true);
                    setMaikoResult(null);
                    try {
                      const res = await api.runSkill("investigate", {
                        context: {
                          query: maikoQuery,
                          context: `Task: ${askingMaiko.title}\nType: ${askingMaiko.type}\nStatus: ${askingMaiko.status}\nProject: ${askingMaiko.project_id || "none"}`,
                          pupdates: "[]", tasks: "[]", calendar: "[]",
                        },
                      });
                      setMaikoResult(res);
                    } catch (err) {
                      setMaikoResult({ error: err.message || "Something went wrong" });
                    }
                    setMaikoRunning(false);
                  }}
                >
                  <Brain size={12} /> {maikoRunning ? "Maiko will get back to you in your inbox!" : "Ask Maiko"}
                </button>
              </div>
              {maikoResult && (
                <div className="md-content" style={{ marginTop: 12 }}>
                  {maikoResult.error
                    ? <p style={{ color: "var(--urgent)" }}>{maikoResult.error}</p>
                    : <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{typeof maikoResult === "string" ? maikoResult : JSON.stringify(maikoResult, null, 2)}</pre>
                  }
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {viewingPlan && (
        <div className="modal-overlay" onClick={() => setViewingPlan(null)}>
          <div className="info-modal" style={{ maxWidth: 650, maxHeight: "80vh" }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <Brain size={14} /> Plan: {viewingPlan.title}
              <span style={{ flex: 1 }} />
              <button className="btn btn-sm" onClick={() => setViewingPlan(null)} style={{ border: "none", padding: 4 }}><X size={14} /></button>
            </div>
            <div className="modal-body" style={{ overflow: "auto" }}>
              <div className="md-content" style={{ fontSize: 13, lineHeight: 1.7, color: "var(--text-dim)", whiteSpace: "pre-wrap" }}>
                {viewingPlan.description || "No plan generated yet. Click 'Plan' on the project header to generate one."}
              </div>
            </div>
          </div>
        </div>
      )}

      {detailTask && (
        <div className="modal-overlay" onClick={() => setDetailTask(null)}>
          <div className="info-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <FolderOpen size={14} /> {detailTask.title}
              <span style={{ flex: 1 }} />
              <button className="btn btn-sm" onClick={() => setDetailTask(null)} style={{ border: "none", padding: 4 }}><X size={14} /></button>
            </div>
            <div className="modal-body" style={{ fontSize: 13, lineHeight: 1.6, color: "var(--text-dim)" }}>
              {(detailTask.extra?.description || detailTask.metadata?.description) && (
                <div style={{ whiteSpace: "pre-wrap", marginBottom: 12 }}>
                  {detailTask.extra?.description || detailTask.metadata?.description}
                </div>
              )}
              {detailTask.url && (
                <a href={detailTask.url} target="_blank" rel="noreferrer" style={{ display: "block", marginBottom: 8 }}>
                  <ExternalLink size={10} /> {detailTask.url}
                </a>
              )}
              {detailTask.tags?.length > 0 && (
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {detailTask.tags.map((tag) => <span key={tag} className="tag">{tag}</span>)}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
