const API_BASE = import.meta.env.DEV ? "http://localhost:8420/api" : "/api";

// Simple GET cache — avoids re-fetching when switching tabs
const _cache = {};
const CACHE_TTL = 5000; // 5 seconds

async function request(path, options = {}) {
  const isGet = !options.method || options.method === "GET";

  // Return cached response for GETs within TTL
  if (isGet && _cache[path] && (Date.now() - _cache[path].at) < CACHE_TTL) {
    return _cache[path].data;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(error.error || res.statusText);
  }
  const data = await res.json();

  // Cache GET responses
  if (isGet) {
    _cache[path] = { data, at: Date.now() };
  }

  return data;
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
  generatePlan: (id) =>
    request(`/projects/${id}/generate-plan`, { method: "POST" }),
  generateTasks: (id) =>
    request(`/projects/${id}/generate-tasks`, { method: "POST" }),
  generatePlan: (projectId) =>
    request(`/projects/${projectId}/generate-plan`, { method: "POST" }),

  // Config
  getConfig: () => request("/config"),
  updateConfig: (data) =>
    request("/config", { method: "PUT", body: JSON.stringify(data) }),

  discoverGithubRepos: () => request("/github/discover", { method: "POST" }),
  testIntegration: (name) => request(`/config/test/${name}`, { method: "POST" }),

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
  refreshScene: () => request("/scene/refresh", { method: "POST" }),

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

  // Pack Insights
  getPackInsightsState: () => request("/pack-insights"),
  startPackInsights: () => request("/pack-insights/start", { method: "POST" }),
  collectPackInsights: () => request("/pack-insights/collect", { method: "POST" }),
  synthesizePackInsights: () => request("/pack-insights/synthesize", { method: "POST" }),
  addPackInsightsLearning: (text, category) =>
    request("/pack-insights/add", { method: "POST", body: JSON.stringify({ text, category }) }),
  finalizePackInsights: (decisions) =>
    request("/pack-insights/finalize", { method: "POST", body: JSON.stringify({ decisions }) }),

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
  createLearning: (data) => request("/learnings", { method: "POST", body: JSON.stringify(data) }),
  backfillKnowledge: (limit = 20) => request("/learnings/backfill", { method: "POST", body: JSON.stringify({ limit }) }),
  approveLearning: (id) => request(`/learnings/${id}/approve`, { method: "POST" }),
  dismissLearning: (id) => request(`/learnings/${id}/dismiss`, { method: "POST" }),

  // Tournaments
  getTournaments: () => request("/tournaments"),
  getTournament: (id) => request(`/tournaments/${id}`),
  runTournament: (repo, pr_number) =>
    request("/tournaments/run", { method: "POST", body: JSON.stringify({ repo, pr_number }) }),
  getTournamentScores: (repo) => {
    const query = repo ? `?repo=${encodeURIComponent(repo)}` : "";
    return request(`/tournaments/scores${query}`);
  },

  // System
  shutdown: () => request("/system/shutdown", { method: "POST" }),

  // Agent terminal & sessions
  openTerminal: (path, taskId, branch) => request("/agents/open-terminal", { method: "POST", body: JSON.stringify({ path, task_id: taskId, branch }) }),
  getAgentSession: (taskId) => request(`/agents/${taskId}/session`),
  resumeAgentSession: (taskId) => request("/agents/resume-session", { method: "POST", body: JSON.stringify({ task_id: taskId }) }),

  // Training
  exportTrainingDataset: (data) => request("/training/export-dataset", { method: "POST", body: JSON.stringify(data || {}) }),
  getTrainingDatasets: () => request("/training/datasets"),
  getTrainingDatasetStats: () => request("/training/dataset-stats"),
  trainAgent: (data) => request("/training/train-agent", { method: "POST", body: JSON.stringify(data) }),
  checkTrainingRequirements: () => request("/training/check-requirements"),
  getTrainingPRs: () => request("/training/prs"),
  getTrainingHistory: () => request("/training/history"),
  runTraining: (data) => request("/training/run", { method: "POST", body: JSON.stringify(data) }),

  // Agent profiles
  getProfiles: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/profiles${query ? `?${query}` : ""}`);
  },
  getProfile: (id) => request(`/profiles/${id}`),
  createProfile: (data) =>
    request("/profiles", { method: "POST", body: JSON.stringify(data) }),
  updateProfile: (id, data) =>
    request(`/profiles/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  archiveProfile: (id) => request(`/profiles/${id}/archive`, { method: "POST" }),
  unarchiveProfile: (id) => request(`/profiles/${id}/unarchive`, { method: "POST" }),
  getAvatars: () => request("/profiles/avatars"),
  recommendAgent: (repo) => request(`/profiles/recommend?repo=${encodeURIComponent(repo || "")}`),

  // Agent assignment
  assignAgent: (data) =>
    request("/agents/assign", { method: "POST", body: JSON.stringify(data) }),
};
