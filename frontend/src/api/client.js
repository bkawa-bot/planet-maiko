const API_BASE = import.meta.env.DEV ? "http://localhost:8420/api" : "/api";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(error.error || res.statusText);
  }
  return res.json();
}

export const api = {
  // Pupdates
  getPupdates: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/pupdates${query ? `?${query}` : ""}`);
  },
  getPupdate: (id) => request(`/pupdates/${id}`),
  createPupdate: (data) =>
    request("/pupdates", { method: "POST", body: JSON.stringify(data) }),
  markRead: (id) => request(`/pupdates/${id}/read`, { method: "POST" }),
  dismissPupdate: (id) =>
    request(`/pupdates/${id}/dismiss`, { method: "POST" }),

  // Tasks
  getTasks: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/tasks${query ? `?${query}` : ""}`);
  },
  getTask: (id) => request(`/tasks/${id}`),
  createTask: (data) =>
    request("/tasks", { method: "POST", body: JSON.stringify(data) }),
  updateTask: (id, data) =>
    request(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  startTask: (id) => request(`/tasks/${id}/start`, { method: "POST" }),
  completeTask: (id) => request(`/tasks/${id}/done`, { method: "POST" }),
  cancelTask: (id) => request(`/tasks/${id}/cancel`, { method: "POST" }),
  importLinear: () => request("/tasks/import-linear", { method: "POST" }),

  // Projects
  getProjects: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/projects${query ? `?${query}` : ""}`);
  },
  getProject: (id) => request(`/projects/${id}`),
  createProject: (data) =>
    request("/projects", { method: "POST", body: JSON.stringify(data) }),
  updateProject: (id, data) =>
    request(`/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  updateProjectStatus: (id, status) =>
    request(`/projects/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),
  generateTasks: (id) =>
    request(`/projects/${id}/generate-tasks`, { method: "POST" }),

  // Config
  getConfig: () => request("/config"),
  updateConfig: (data) =>
    request("/config", { method: "PUT", body: JSON.stringify(data) }),

  // Pollers
  getPollerStatus: () => request("/pollers/status"),
  runPoller: (name) => request(`/pollers/${name}/run`, { method: "POST" }),

  // Brain
  getBrainStatus: () => request("/brain/status"),
  getBrainRules: () => request("/brain/rules"),
  runBrainCycle: () => request("/brain/cycle", { method: "POST" }),
  getSchedule: () => request("/brain/schedule"),

  // Scene
  getScene: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/scene${query ? `?${query}` : ""}`);
  },

  // Focus
  getFocus: () => request("/focus"),
  setFocus: (state, duration_minutes) =>
    request("/focus", { method: "POST", body: JSON.stringify({ state, duration_minutes }) }),
  getFocusDigest: () => request("/focus/digest"),

  // Learnings
  getLearnings: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/learnings${query ? `?${query}` : ""}`);
  },
  getLearningBrief: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/learnings/brief${query ? `?${query}` : ""}`);
  },

  // EOD
  getEodState: () => request("/eod"),
  startEod: () => request("/eod/start", { method: "POST" }),
  collectEod: () => request("/eod/collect", { method: "POST" }),
  synthesizeEod: () => request("/eod/synthesize", { method: "POST" }),
  finalizeEod: (decisions) =>
    request("/eod/finalize", { method: "POST", body: JSON.stringify({ decisions }) }),

  // Agents
  getAgents: () => request("/agents"),
  getAgentActivity: () => request("/agents/activity"),
  getAgentMessages: (taskId) => request(`/agents/${taskId}/messages`),
  sendToAgent: (taskId, data) =>
    request(`/agents/${taskId}/inbox`, { method: "POST", body: JSON.stringify(data) }),
  getConflicts: () => request("/agents/conflicts"),

  // Skills
  getSkills: () => request("/skills"),
  getSkill: (id) => request(`/skills/${id}`),
  createSkill: (data) => request("/skills", { method: "POST", body: JSON.stringify(data) }),
  updateSkill: (id, data) => request(`/skills/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteSkill: (id) => request(`/skills/${id}`, { method: "DELETE" }),
  runSkill: (name, data) =>
    request(`/skills/${name}/run`, { method: "POST", body: JSON.stringify(data) }),
  getSkillResults: (skillName) => {
    const query = skillName ? `?skill_name=${encodeURIComponent(skillName)}` : "";
    return request(`/skill-results${query}`);
  },
  getSkillResult: (id) => request(`/skill-results/${id}`),

  // Suggestions
  runScan: (repos) =>
    request("/suggestions/scan", { method: "POST", body: JSON.stringify({ repos }) }),

  // Expertise
  getExpertise: () => request("/expertise"),
  getExperts: (repo) => request(`/expertise/experts?repo=${encodeURIComponent(repo)}`),

  // Signals
  getSignals: () => request("/signals"),

  // Learnings management
  approveLearning: (id) => request(`/learnings/${id}/approve`, { method: "POST" }),
  dismissLearning: (id) => request(`/learnings/${id}/dismiss`, { method: "POST" }),

  // Agent profiles
  getProfiles: () => request("/profiles"),
  getProfile: (id) => request(`/profiles/${id}`),
  createProfile: (data) =>
    request("/profiles", { method: "POST", body: JSON.stringify(data) }),
  updateProfile: (id, data) =>
    request(`/profiles/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  getAvatars: () => request("/profiles/avatars"),
  recommendAgent: (repo) => request(`/profiles/recommend?repo=${encodeURIComponent(repo || "")}`),

  // Agent assignment
  assignAgent: (data) =>
    request("/agents/assign", { method: "POST", body: JSON.stringify(data) }),
};
