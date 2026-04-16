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

  if (isGet) {
    _cache[path] = { data, at: Date.now() };
  } else {
    // Any mutation invalidates GET cache so the next read sees fresh state.
    // This was a real bug: Dismiss All posted to N /learnings/*/dismiss
    // endpoints and then fetchLearnings was served from the stale cache,
    // making the UI look as if nothing changed.
    for (const key in _cache) delete _cache[key];
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
  reassignTask: (id, agent_id) =>
    request(`/tasks/${id}/reassign`, { method: "POST", body: JSON.stringify(agent_id ? { agent_id } : {}) }),
  launchTask: (id) => request(`/tasks/${id}/launch`, { method: "POST" }),
  sendTaskToLinear: (id, overrides = {}) =>
    request(`/tasks/${id}/linear`, { method: "POST", body: JSON.stringify(overrides) }),
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
  reviseTasks: (id, feedback, currentTasks) =>
    request(`/projects/${id}/revise-tasks`, {
      method: "POST",
      body: JSON.stringify({ feedback, current_tasks: currentTasks }),
    }),

  // Config
  getConfig: () => request("/config"),
  updateConfig: (data) =>
    request("/config", { method: "PUT", body: JSON.stringify(data) }),

  discoverGithubRepos: () => request("/github/discover", { method: "POST" }),
  testIntegration: (name) => request(`/config/test/${name}`, { method: "POST" }),
  getLinearTeams: () => request("/config/linear/teams"),

  // Pollers
  getPollerStatus: () => request("/pollers/status"),
  runPoller: (name) => request(`/pollers/${name}/run`, { method: "POST" }),

  // Brain
  getBrainStatus: () => request("/brain/status"),
  getBrainRules: () => request("/brain/rules"),
  runBrainCycle: () => request("/brain/cycle", { method: "POST" }),
  getSchedule: () => request("/brain/schedule"),
  regenerateSchedule: (instructions) =>
    request("/brain/schedule/regenerate", { method: "POST", body: JSON.stringify({ instructions }) }),
  clearScheduleOverride: () => request("/brain/schedule/override", { method: "DELETE" }),

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
  getSignals: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/signals${query ? `?${query}` : ""}`);
  },
  getSignalsCount: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/signals/count${query ? `?${query}` : ""}`);
  },

  // Diff review — feeds the ReviewDiff page
  getTaskDiff: (taskId) => request(`/tasks/${taskId}/diff`),
  listDiffComments: (taskId) => request(`/tasks/${taskId}/comments`),
  createDiffComment: (taskId, data) =>
    request(`/tasks/${taskId}/comments`, { method: "POST", body: JSON.stringify(data) }),
  updateDiffComment: (commentId, data) =>
    request(`/comments/${commentId}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteDiffComment: (commentId) =>
    request(`/comments/${commentId}`, { method: "DELETE" }),
  requestDiffChanges: (taskId) =>
    request(`/tasks/${taskId}/review/request-changes`, { method: "POST" }),
  approveDiffReview: (taskId) =>
    request(`/tasks/${taskId}/review/approve`, { method: "POST" }),

  // Plan mode (per-task)
  getTaskPlan: (taskId) => request(`/tasks/${taskId}/plan`),
  approveTaskPlan: (taskId) =>
    request(`/tasks/${taskId}/plan/approve`, { method: "POST" }),
  reviseTaskPlan: (taskId, feedback) =>
    request(`/tasks/${taskId}/plan/revise`, {
      method: "POST",
      body: JSON.stringify({ feedback }),
    }),

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
  getQueuedAgentTasks: () => request("/agents/queued"),
  getAgentMessages: (taskId) => request(`/agents/${taskId}/messages`),
  sendToAgent: (taskId, data) =>
    request(`/agents/${taskId}/inbox`, { method: "POST", body: JSON.stringify(data) }),
  nudgeAgent: (taskId) =>
    request(`/agents/${taskId}/nudge`, { method: "POST" }),
  rerunAgent: (taskId) =>
    request(`/agents/${taskId}/rerun`, { method: "POST" }),
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

  // Learnings management
  createLearning: (data) => request("/learnings", { method: "POST", body: JSON.stringify(data) }),
  backfillKnowledge: (limit = null, repo = null) => {
    // limit=null means "all comments in the repo"; don't send the
    // field at all so the backend default kicks in.
    const body = {};
    if (limit != null) body.limit = limit;
    if (repo) body.repo = repo;
    return request("/learnings/backfill", { method: "POST", body: JSON.stringify(body) });
  },
  getBackfillStatus: () => request("/learnings/backfill/status"),
  clusterLearnings: () => request("/learnings/cluster", { method: "POST" }),
  approveLearning: (id) => request(`/learnings/${id}/approve`, { method: "POST" }),
  dismissLearning: (id) => request(`/learnings/${id}/dismiss`, { method: "POST" }),
  classifyLearnings: (batchSize = 50) => request("/learnings/classify", { method: "POST", body: JSON.stringify({ batch_size: batchSize }) }),

  // Insights (Team Playbook — tribal / operational notes injected into CLAUDE.md)
  getInsights: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/insights${query ? `?${query}` : ""}`);
  },
  createInsight: (data) =>
    request("/insights", { method: "POST", body: JSON.stringify(data) }),
  updateInsight: (id, data) =>
    request(`/insights/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  approveInsight: (id) =>
    request(`/insights/${id}/approve`, { method: "POST" }),
  dismissInsight: (id) =>
    request(`/insights/${id}/dismiss`, { method: "POST" }),
  confirmInsight: (id) =>
    request(`/insights/${id}/confirm`, { method: "POST" }),
  deleteInsight: (id) =>
    request(`/insights/${id}`, { method: "DELETE" }),

  // Plugins
  getPlugins: () => request("/plugins"),
  togglePlugin: (name) => request(`/plugins/${name}/toggle`, { method: "POST" }),

  // Project plan orchestration
  approveProjectPlan: (projectId, tasks) =>
    request(`/projects/${projectId}/approve-plan`, { method: "POST", body: JSON.stringify({ tasks }) }),

  // Agent proposals (From Maiko approval queue)
  createProposal: (data) => request("/proposals", { method: "POST", body: JSON.stringify(data) }),
  approveProposal: (id, draft) => request(`/proposals/${id}/approve`, { method: "POST", body: JSON.stringify(draft ? { draft } : {}) }),
  dismissProposal: (id) => request(`/proposals/${id}/dismiss`, { method: "POST" }),

  // Custom themes
  getThemes: () => request("/themes"),
  getTheme: (id) => request(`/themes/${id}`),
  saveTheme: (data) => request("/themes", { method: "POST", body: JSON.stringify(data) }),
  deleteTheme: (id) => request(`/themes/${id}`, { method: "DELETE" }),
  generateTheme: (query) => request("/themes/generate", { method: "POST", body: JSON.stringify({ query }) }),

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
  getTrainingProgress: () => request("/training/progress"),
  generateFromRules: (data) => request("/training/generate-from-rules", { method: "POST", body: JSON.stringify(data || {}) }),
  getRuleCoverage: (repo) => request(`/training/rule-coverage${repo ? `?repo=${encodeURIComponent(repo)}` : ""}`),
  generateSynthetic: (data) => request("/training/generate-synthetic", { method: "POST", body: JSON.stringify(data || {}) }),
  getAdapters: () => request("/training/adapters"),
  assignAdapter: (data) => request("/training/assign-adapter", { method: "POST", body: JSON.stringify(data) }),

  // Chat
  chat: (message) => request("/chat", { method: "POST", body: JSON.stringify({ message }) }),

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

  // Agent assignment
  assignAgent: (data) =>
    request("/agents/assign", { method: "POST", body: JSON.stringify(data) }),
};
