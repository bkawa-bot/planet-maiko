const API_BASE = import.meta.env.DEV ? "http://localhost:8420/api" : "/api";

// Simple GET cache — avoids re-fetching when switching tabs
const _cache = {};
const CACHE_TTL = 5000; // 5 seconds

async function request(path, options = {}) {
  // timeoutMs is our own option — strip it before handing to fetch.
  const { timeoutMs, ...fetchOpts } = options;
  const isGet = !fetchOpts.method || fetchOpts.method === "GET";

  // Return cached response for GETs within TTL
  if (isGet && _cache[path] && (Date.now() - _cache[path].at) < CACHE_TTL) {
    return _cache[path].data;
  }

  const controller = timeoutMs ? new AbortController() : null;
  const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : null;

  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      signal: controller?.signal,
      ...fetchOpts,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new Error("Request timed out — try again in a moment.");
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // Different endpoints use different error keys — /config/test/*
    // returns `message`, others return `error`. Try both before
    // falling back to the raw status text, otherwise users see
    // "BAD REQUEST" instead of the actual reason.
    throw new Error(body.error || body.message || res.statusText);
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
  getLinearTeamMeta: (teamId) => {
    const q = teamId ? `?team_id=${encodeURIComponent(teamId)}` : "";
    return request(`/config/linear/team-meta${q}`);
  },

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
  getPackInsightsGatheringReplies: () => request("/pack-insights/gathering-replies"),
  resetPackInsights: () => request("/pack-insights/reset", { method: "POST" }),
  wrapUpPackInsights: (droppedMessageIds) =>
    request("/pack-insights/wrap-up", {
      method: "POST",
      body: JSON.stringify({ dropped_message_ids: droppedMessageIds || [] }),
    }),

  // Agents
  getAgents: () => request("/agents"),
  getAgentActivity: () => request("/agents/activity"),
  getPackRequests: () => request("/agents/requests"),
  getQueuedAgentTasks: () => request("/agents/queued"),
  getAgentMessages: (taskId) => request(`/agents/${taskId}/messages`),
  sendToAgent: (taskId, data) =>
    request(`/agents/${taskId}/inbox`, { method: "POST", body: JSON.stringify(data) }),
  nudgeAgent: (taskId) =>
    request(`/agents/${taskId}/nudge`, { method: "POST" }),
  rerunAgent: (taskId) =>
    request(`/agents/${taskId}/rerun`, { method: "POST" }),
  getConflicts: () => request("/agents/conflicts"),

  // External sessions — registered by external orchestrators via the maiko-brain MCP
  getExternalSessions: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/sessions${query ? `?${query}` : ""}`);
  },

  // Skills
  getSkills: () => request("/skills"),
  getSkill: (id) => request(`/skills/${id}`),
  createSkill: (data) => request("/skills", { method: "POST", body: JSON.stringify(data) }),
  updateSkill: (id, data) => request(`/skills/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteSkill: (id) => request(`/skills/${id}`, { method: "DELETE" }),
  runSkill: (name, data) =>
    request(`/skills/${name}/run`, { method: "POST", body: JSON.stringify(data) }),

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
  // RAG retrieval health — backend (none = offline), model name,
  // and how many active rules have a current embedding ready for
  // cosine matching. Drives the BrainView status pill.
  getRagStatus: () => request("/rules/embedding-status"),
  // Free-text or diff-based rule retrieval. Either `diff` (string)
  // or `queries` (string[]) is required. Server runs Haiku
  // decomposition on the diff path; queries skip it.
  getRelevantRules: (body) =>
    request("/rules/relevant", { method: "POST", body: JSON.stringify(body) }),
  clusterLearnings: () => request("/learnings/cluster", { method: "POST" }),
  approveLearning: (id) => request(`/learnings/${id}/approve`, { method: "POST" }),
  dismissLearning: (id) => request(`/learnings/${id}/dismiss`, { method: "POST" }),
  // Drain thin / old pending learnings in bulk. Body: {max_signal_count,
  // older_than_days, dry_run}. With dry_run=true, returns count + sample
  // for a preview before the user confirms.
  bulkDismissPendingLearnings: (body) =>
    request("/learnings/bulk-dismiss", { method: "POST", body: JSON.stringify(body) }),
  updateLearning: (id, data) =>
    request(`/learnings/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  getLearning: (id) => request(`/learnings/${id}`),
  classifyLearnings: (batchSize = 50) => request("/learnings/classify", { method: "POST", body: JSON.stringify({ batch_size: batchSize }) }),

  // Automations (unified when/then model; replaced goals)
  getAutomations: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/automations${query ? `?${query}` : ""}`);
  },
  createAutomation: (data) => request("/automations", { method: "POST", body: JSON.stringify(data) }),
  updateAutomation: (id, data) => request(`/automations/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteAutomation: (id) => request(`/automations/${id}`, { method: "DELETE" }),

  // Agent jobs (pack-owned one-shot runs)
  getAgentJobs: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request(`/agent-jobs${query ? `?${query}` : ""}`);
  },
  getAgentJob: (id) => request(`/agent-jobs/${id}`),
  approveAgentJob: (id) => request(`/agent-jobs/${id}/approve`, { method: "POST" }),
  cancelAgentJob: (id) => request(`/agent-jobs/${id}/cancel`, { method: "POST" }),
  ackAgentJob: (id) => request(`/agent-jobs/${id}/ack`, { method: "POST" }),
  deleteAgentJob: (id) => request(`/agent-jobs/${id}`, { method: "DELETE" }),

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
  cartographRepo: (repo) =>
    request("/insights/cartograph", { method: "POST", body: JSON.stringify({ repo }) }),

  // Plugins
  getPlugins: () => request("/plugins"),
  togglePlugin: (name) => request(`/plugins/${name}/toggle`, { method: "POST" }),
  // Pupdate type registry — built-ins + anything plugins register via
  // register_pupdate_types(). Drives the Automation editor's dropdown.
  getPupdateTypes: () => request("/pupdate-types"),
  // Pupdate source registry — poller names + the "maiko"/"agent"
  // built-ins. Also drives the Automation editor's autocomplete.
  getPupdateSources: () => request("/pupdate-sources"),

  // Project plan orchestration
  approveProjectPlan: (projectId, tasks) =>
    request(`/projects/${projectId}/approve-plan`, { method: "POST", body: JSON.stringify({ tasks }) }),

  // Agent proposals (From Maiko approval queue)
  createProposal: (data) => request("/proposals", { method: "POST", body: JSON.stringify(data) }),
  approveProposal: (id, draft) => request(`/proposals/${id}/approve`, { method: "POST", body: JSON.stringify(draft ? { draft } : {}) }),
  approveProposalAsGoal: (id) => request(`/proposals/${id}/approve-as-goal`, { method: "POST" }),
  dismissProposal: (id) => request(`/proposals/${id}/dismiss`, { method: "POST" }),

  // Custom themes
  getThemes: () => request("/themes"),
  getTheme: (id) => request(`/themes/${id}`),
  saveTheme: (data) => request("/themes", { method: "POST", body: JSON.stringify(data) }),
  deleteTheme: (id) => request(`/themes/${id}`, { method: "DELETE" }),
  generateTheme: (query) => request("/themes/generate", { method: "POST", body: JSON.stringify({ query }) }),

  // Home overview — the rolling LLM-generated pane
  getHomeOverview: () => request("/home/overview"),
  getShippedToday: () => request("/home/shipped-today"),

  // Memos — canonical surface for persistent user-facing items
  // (skill results, notifications, agent asks, job approvals, etc.).
  // The URLSearchParams handles arrays for repeated ?status= filters.
  getMemos: (params = {}) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "") continue;
      if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
      else qs.append(k, String(v));
    }
    const q = qs.toString();
    return request(`/memos${q ? `?${q}` : ""}`);
  },
  getMemo: (id) => request(`/memos/${id}`),
  markMemoSeen: (id) => request(`/memos/${id}/mark-seen`, { method: "POST" }),
  dismissMemo: (id) => request(`/memos/${id}/dismiss`, { method: "POST" }),
  approveMemo: (id) => request(`/memos/${id}/approve`, { method: "POST" }),
  // Promote a memo (typically a notification) into actionable work.
  // Both pull pupdate_snapshot from memo.extra for context. Marks
  // the memo actioned on success so it disappears from the pane.
  createTaskFromMemo: (id, body = {}) =>
    request(`/memos/${id}/create-task`, { method: "POST", body: JSON.stringify(body) }),
  launchAgentFromMemo: (id, body = {}) =>
    request(`/memos/${id}/launch-agent`, { method: "POST", body: JSON.stringify(body) }),
  updateMemo: (id, data) =>
    request(`/memos/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  // Canonical "waiting on your review" list — plans to approve, diffs
  // to read, pack-owned artifacts to skim. Exhaustive, not curated.
  getReviewQueue: () => request("/home/review-queue"),
  refreshHomeOverview: () => request("/home/overview/refresh", { method: "POST" }),

  // System
  shutdown: () => request("/system/shutdown", { method: "POST" }),
  getSystemHealth: () => request("/system/health"),
  getToday: () => request("/today"),

  // Shutdown / cleanup ritual (power button on the nav)
  getShutdownPreview: () => request("/shutdown/preview"),
  runShutdownStep: (name) =>
    request("/shutdown/step", { method: "POST", body: JSON.stringify({ name }) }),

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
  getRuleGenProgress: () => request("/training/generate-from-rules/progress"),
  getRuleCoverage: (repo) => request(`/training/rule-coverage${repo ? `?repo=${encodeURIComponent(repo)}` : ""}`),
  generateSynthetic: (data) => request("/training/generate-synthetic", { method: "POST", body: JSON.stringify(data || {}) }),
  getAdapters: () => request("/training/adapters"),
  getBaseModels: () => request("/training/base-models"),

  // Chat
  chat: (message) => request("/chat", { method: "POST", body: JSON.stringify({ message }) }),

  // Repo checkers — auto-detect + run
  detectChecks: (repo_path) => request(`/checks?repo_path=${encodeURIComponent(repo_path)}`),
  runChecks: (data) => request("/checks/run", { method: "POST", body: JSON.stringify(data) }),

  // Pet Maiko — community counter + owner log
  petMaiko: (note) =>
    request("/maiko/pet", { method: "POST", body: JSON.stringify({ note: note || "" }) }),
  getPetCount: () => request("/maiko/pets/count"),
  getPetLog: (params = {}) => {
    const q = new URLSearchParams(params).toString();
    return request(`/maiko/pets/log${q ? `?${q}` : ""}`);
  },
  markPetIrl: (id) => request(`/maiko/pets/${id}/mark_irl`, { method: "POST" }),
  markAllPetsIrl: () => request("/maiko/pets/mark_all_irl", { method: "POST" }),

  // Ask the Pack — natural-language dispatcher that picks an agent and launches them.
  // Backend calls an LLM router with a 45s ceiling; we give the network a small cushion
  // and fail loudly instead of hanging forever.
  dispatchPack: (request_text, context, non_goals) =>
    request("/pack/dispatch", {
      method: "POST",
      timeoutMs: 60_000,
      body: JSON.stringify({
        request: request_text,
        context: context || "",
        non_goals: non_goals || "",
      }),
    }),

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
  getCards: () => request("/cards"),

  // Agent assignment
  assignAgent: (data) =>
    request("/agents/assign", { method: "POST", body: JSON.stringify(data) }),
};
