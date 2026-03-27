import { useEffect, useState } from "react";
import { api } from "../api/client";
import "./Projects.css";

const STATUS_OPTIONS = ["planning", "approved", "active", "paused", "done"];

export default function Projects() {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [expanded, setExpanded] = useState(null);
  const [detail, setDetail] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ title: "", description: "", priority: "normal" });

  const fetchProjects = async () => {
    setLoading(true);
    try {
      const params = {};
      if (statusFilter) params.status = statusFilter;
      setProjects(await api.getProjects(params));
    } catch (err) {
      console.error("Failed to fetch projects:", err);
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchProjects();
  }, [statusFilter]);

  const handleExpand = async (id) => {
    if (expanded === id) {
      setExpanded(null);
      setDetail(null);
      return;
    }
    try {
      const data = await api.getProject(id);
      setDetail(data);
      setExpanded(id);
    } catch (err) {
      console.error("Failed to fetch project:", err);
    }
  };

  const handleStatusChange = async (id, newStatus) => {
    try {
      await api.updateProjectStatus(id, newStatus);
      fetchProjects();
      if (expanded === id) {
        const data = await api.getProject(id);
        setDetail(data);
      }
    } catch (err) {
      console.error("Failed to update status:", err);
    }
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!form.title.trim()) return;
    try {
      const id = `proj-${Date.now()}`;
      await api.createProject({ id, ...form });
      setForm({ title: "", description: "", priority: "normal" });
      setShowForm(false);
      fetchProjects();
    } catch (err) {
      console.error("Failed to create project:", err);
    }
  };

  return (
    <div className="projects-page">
      <div className="projects-header">
        <h2>Projects</h2>
        <div className="projects-controls">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button className="btn-primary" onClick={() => setShowForm(!showForm)}>
            {showForm ? "Cancel" : "+ New Project"}
          </button>
        </div>
      </div>

      {showForm && (
        <form className="project-form" onSubmit={handleCreate}>
          <input
            type="text"
            placeholder="Project title..."
            value={form.title}
            onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
            autoFocus
          />
          <input
            type="text"
            placeholder="Description (optional)"
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
          />
          <select
            value={form.priority}
            onChange={(e) => setForm((f) => ({ ...f, priority: e.target.value }))}
          >
            <option value="low">Low</option>
            <option value="normal">Normal</option>
            <option value="high">High</option>
            <option value="urgent">Urgent</option>
          </select>
          <button type="submit" className="btn-primary">Create</button>
        </form>
      )}

      {loading ? (
        <p className="projects-empty">Loading...</p>
      ) : projects.length === 0 ? (
        <p className="projects-empty">No projects yet.</p>
      ) : (
        <ul className="project-list">
          {projects.map((p) => (
            <li key={p.id} className="project-item">
              <div
                className="project-row"
                onClick={() => handleExpand(p.id)}
              >
                <span className={`status-badge ${p.status}`}>{p.status}</span>
                <span className={`priority-dot ${p.priority}`} />
                <div className="project-info">
                  <span className="project-title">{p.title}</span>
                  {p.description && (
                    <span className="project-desc">{p.description}</span>
                  )}
                </div>
                <span className="expand-arrow">
                  {expanded === p.id ? "\u25BC" : "\u25B6"}
                </span>
              </div>

              {expanded === p.id && detail && (
                <div className="project-detail">
                  <div className="detail-status-controls">
                    <span>Status:</span>
                    {STATUS_OPTIONS.map((s) => (
                      <button
                        key={s}
                        className={`status-btn ${p.status === s ? "active" : ""}`}
                        onClick={() => handleStatusChange(p.id, s)}
                      >
                        {s}
                      </button>
                    ))}
                  </div>

                  {detail.source_type && (
                    <div className="detail-source">
                      Source: {detail.source_type}
                      {detail.source_id && ` (${detail.source_id})`}
                      {detail.source_url && (
                        <a href={detail.source_url} target="_blank" rel="noreferrer"> Open</a>
                      )}
                    </div>
                  )}

                  <div className="project-tasks">
                    <h4>Tasks ({detail.tasks?.length || 0})</h4>
                    {detail.tasks?.length > 0 ? (
                      <ul className="mini-task-list">
                        {detail.tasks.map((t) => (
                          <li key={t.id}>
                            <span className={`status-badge ${t.status}`}>
                              {t.status.replace("_", " ")}
                            </span>
                            <span>{t.title}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="no-tasks">No tasks linked to this project yet.</p>
                    )}
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
