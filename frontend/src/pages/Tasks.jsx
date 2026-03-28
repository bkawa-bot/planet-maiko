import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import AssignAgentModal from "../components/AssignAgentModal";
import {
  CheckSquare, Plus, FolderPlus, Pin, PinOff, ExternalLink,
  ChevronDown, ChevronRight, Folder, GitBranch, Clock, Bot,
  Play, X, Download, Sparkles, Trash2,
} from "lucide-react";
import "./Tasks.css";

const STATUS_COLORS = {
  new: "var(--text-muted)", in_progress: "#60a5fa", waiting: "#fbbf24",
  review: "#a78bfa", done: "#4ade80", cancelled: "#6b7280",
};

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
  const [taskForm, setTaskForm] = useState({ title: "", type: "todo", priority: "normal", url: "", project_id: "", due_date: "" });
  const [projectForm, setProjectForm] = useState({ title: "", description: "", priority: "normal" });

  const fetchData = async () => {
    setLoading(true);
    try {
      const [t, p] = await Promise.all([api.getTasks(), api.getProjects()]);
      setTasks(t);
      setProjects(p);
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
    await api.createTask({ id: `task-${Date.now()}`, ...taskForm });
    setTaskForm({ title: "", type: "todo", priority: "normal", url: "", project_id: "" });
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
        <button className="btn" onClick={async () => {
          showToast("Importing from Linear... 📥", "normal");
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
      </div>

      {/* Task creation form */}
      {showTaskForm && (
        <form className="create-form card" onSubmit={handleCreateTask}>
          <div className="form-row">
            <label>
              Title <span className="required">*</span>
              <input type="text" value={taskForm.title} onChange={(e) => setTaskForm((f) => ({ ...f, title: e.target.value }))} autoFocus />
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
      )}

      {/* Project creation form */}
      {showProjectForm && (
        <form className="create-form card" onSubmit={handleCreateProject}>
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
                  <button className="btn btn-sm btn-action" onClick={async (e) => {
                    e.stopPropagation();
                    setGenerating(project.id);
                    showToast("Maiko is thinking up tasks... 🐕", "normal");
                    try {
                      const result = await api.generateTasks(project.id);
                      setGeneratedTasks(result);
                      showToast(`Generated ${result.tasks.length} task ideas!`, "normal");
                    } catch (err) {
                      showToast(err.message || "Couldn't generate tasks", "high");
                    }
                    setGenerating(null);
                  }} disabled={generating === project.id}>
                    <Sparkles size={10} /> {generating === project.id ? "..." : "Generate"}
                  </button>
                  {project.source_url && (
                    <a href={project.source_url} target="_blank" rel="noreferrer" className="btn btn-sm" onClick={(e) => e.stopPropagation()}>
                      <ExternalLink size={10} />
                    </a>
                  )}
                </div>
                {!collapsed && (pts.length > 0
                  ? pts.map((t) => renderTaskCard(t, expanded, setExpanded, handleAction, projects, fetchData, setAssigningTask))
                  : <div className="project-empty">No tasks yet. Create one and assign it to this project.</div>
                )}
              </div>
            );
          })}

          {/* Ungrouped tasks */}
          {ungrouped.filter((t) => t.status !== "done" && t.status !== "cancelled").map((t) =>
            renderTaskCard(t, expanded, setExpanded, handleAction, projects, fetchData, setAssigningTask)
          )}

          {/* Done tasks (collapsed) */}
          {tasks.filter((t) => t.status === "done" || t.status === "cancelled").length > 0 && (
            <div className="done-section">
              <div className="done-header" onClick={() => toggleGroup("_done")}>
                {collapsedGroups["_done"] ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                <span>Completed ({tasks.filter((t) => t.status === "done" || t.status === "cancelled").length})</span>
              </div>
              {!collapsedGroups["_done"] && tasks.filter((t) => t.status === "done" || t.status === "cancelled").map((t) =>
                renderTaskCard(t, expanded, setExpanded, handleAction, projects, fetchData, setAssigningTask)
              )}
            </div>
          )}
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
                  for (const gt of generatedTasks.tasks) {
                    await api.createTask({
                      id: `task-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                      title: gt.title,
                      type: gt.type || "todo",
                      priority: gt.priority || "normal",
                      project_id: generatedTasks.project_id,
                    });
                  }
                  showToast(`Created ${generatedTasks.tasks.length} task(s)! 🎉`, "normal");
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
    </div>
  );
}

function renderTaskCard(t, expanded, setExpanded, handleAction, projects, fetchData, setAssigningTask) {
  const isExpanded = expanded === t.id;
  const statusColor = {
    new: "var(--text-muted)", in_progress: "#60a5fa", waiting: "#fbbf24",
    review: "#a78bfa", done: "#4ade80", cancelled: "#6b7280",
  }[t.status] || "var(--text-muted)";

  const statusIcon = {
    new: "📋", in_progress: "🔧", waiting: "⏳",
    review: "👀", done: "✅", cancelled: "⛔",
  }[t.status] || "📋";

  return (
    <div
      key={t.id}
      className={`task-card ${t.status === "done" || t.status === "cancelled" ? "dimmed" : ""} ${isExpanded ? "expanded" : ""}`}
      onClick={() => setExpanded(isExpanded ? null : t.id)}
    >
      <div className="task-status-indicator" style={{ background: statusColor }} />
      <div className="task-icon" style={{ borderColor: statusColor }}>
        <span className="task-icon-emoji">{statusIcon}</span>
      </div>
      <div className="task-content">
        <div className="task-top">
          <span className="task-title">
            {t.url ? <a href={t.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{t.title}</a> : t.title}
          </span>
        </div>
        <div className="task-meta">
          <span className="task-status-label" style={{ color: statusColor }}>{t.status.replace("_", " ")}</span>
          {t.project_id && <span className="tag tag-project">{t.project_id}</span>}
          {(t.metadata?.repo || t.extra?.repo) && <span className="tag"><GitBranch size={9} /> {t.metadata?.repo || t.extra?.repo}</span>}
          <span className="task-type-label">{t.type}</span>
          {t.due_date && <span className={`tag ${new Date(t.due_date) < new Date() ? "tag-overdue" : "tag-due"}`}><Clock size={9} /> {t.due_date}</span>}
          {!t.due_date && t.updated_at && <span className="task-time"><Clock size={9} /> {new Date(t.updated_at).toLocaleDateString()}</span>}
          {t.assigned_agent_id && <span className="tag agent-assigned-chip"><Bot size={9} /> {t.assigned_agent_id.replace("agent-", "")}</span>}
        </div>

        {isExpanded && (
          <>
            {t.tags?.length > 0 && (
              <div className="task-detail">
                {t.tags.map((tag) => <span key={tag} className="tag">{tag}</span>)}
              </div>
            )}
            <div className="task-inline-actions" onClick={(e) => e.stopPropagation()}>
              <input
                type="date"
                className="task-due-input"
                value={t.due_date || ""}
                onChange={async (e) => {
                  await api.updateTask(t.id, { due_date: e.target.value || null });
                  showToast(e.target.value ? `Due: ${e.target.value}` : "Due date cleared", "normal");
                  fetchData();
                }}
                title="Set due date"
              />
              <select
                className="task-project-select"
                value={t.project_id || ""}
                onChange={async (e) => {
                  await api.updateTask(t.id, { project_id: e.target.value || null });
                  showToast(e.target.value ? "Moved to project" : "Removed from project", "normal");
                  fetchData();
                }}
              >
                <option value="">No project</option>
                {(projects || []).map((p) => (
                  <option key={p.id} value={p.id}>{p.title}</option>
                ))}
              </select>
              {t.status === "new" && (
                <button className="btn btn-sm btn-approve" onClick={(e) => handleAction(e, t.id, "start")}>
                  <Play size={10} /> Start
                </button>
              )}
              {(t.status === "new" || t.status === "in_progress") && (
                <button className="btn btn-sm btn-create" onClick={(e) => handleAction(e, t.id, "done")}>
                  <CheckSquare size={10} /> Done
                </button>
              )}
              {t.url && (
                <a href={t.url} target="_blank" rel="noreferrer" className="btn btn-sm" onClick={(e) => e.stopPropagation()}>
                  <ExternalLink size={10} /> Open
                </a>
              )}
              {t.status !== "done" && t.status !== "cancelled" && !t.assigned_agent_id && (
                <button className="btn btn-sm btn-action" onClick={() => setAssigningTask(t)}>
                  <Bot size={10} /> Assign Agent
                </button>
              )}
              {t.status !== "done" && t.status !== "cancelled" && (
                <button className="btn btn-sm btn-danger" onClick={(e) => handleAction(e, t.id, "cancel")}>
                  <X size={10} /> Cancel
                </button>
              )}
            </div>
          </>
        )}
      </div>
      <ChevronRight size={14} className={`task-chevron ${isExpanded ? "open" : ""}`} />
    </div>
  );
}
