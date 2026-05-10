// Flask serves the bundled frontend at :8420, so when window.origin is
// that, a relative `/api` is right. In Tauri (window.origin is
// `tauri://localhost` or similar) and in Vite dev (`http://localhost:5173`)
// we cross origins to hit Flask, so we need the absolute URL. CORS is
// enabled on the backend (see CORS(app) in app.py).
const FLASK_ORIGIN = "http://localhost:8420";
const API_BASE =
  typeof window !== "undefined" && window.location.origin === FLASK_ORIGIN
    ? "/api"
    : `${FLASK_ORIGIN}/api`;

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
    const err = new Error(body.error || body.message || res.statusText);
    // Preserve the parsed body + status on the Error so callers
    // can act on structured failures — e.g. 422 with needs_input
    // payload from /memos/<id>/approve when it can't proceed
    // without a repo pick.
    err.status = res.status;
    err.body = body;
    throw err;
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
  // Bring a soft-cancelled task back. Worktree must still be on disk;
  // returns 410 if it was cleaned up.
  reviveTask: (id) => request(`/tasks/${id}/revive`, { method: "POST" }),
  // Hard-delete a cancelled or done task. The escape hatch from the
  // soft-delete pattern — use after cancel if the task is truly gone.
  forgetTask: (id) => request(`/tasks/${id}/forget`, { method: "POST" }),
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
  createProject: (data) =>
    request("/projects", { method: "POST", body: JSON.stringify(data) }),
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
  runBrainCycle: () => request("/brain/cycle", { method: "POST" }),

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
  getQueuedAgentTasks: () => request("/agents/queued"),
  getAgentMessages: (taskId) => request(`/agents/${taskId}/messages`),
  sendToAgent: (taskId, data) =>
    request(`/agents/${taskId}/inbox`, { method: "POST", body: JSON.stringify(data) }),
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
  // Manual full-sweep clustering across every active+pending Learning,
  // category by category. Async — the POST returns 202 with the
  // initial progress dict, the frontend polls /cluster/status to
  // drive the progress bar.
  clusterLearnings: () => request("/learnings/cluster", { method: "POST" }),
  getClusterStatus: () => request("/learnings/cluster/status"),

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
  // Bring a cancelled job back. Worktree must still be on disk.
  reviveAgentJob: (id) => request(`/agent-jobs/${id}/revive`, { method: "POST" }),
  // Re-queue a FAILED job so the next cycle picks it up. Distinct
  // from revive, which is for cancelled jobs — the backend gates on
  // status to keep the two flows from cross-firing.
  retryAgentJob: (id) => request(`/agent-jobs/${id}/retry`, { method: "POST" }),
  // Hard delete — row + worktree. The "I'm sure" escape hatch from
  // soft-cancel. Backend refuses on running jobs.
  deleteAgentJob: (id) => request(`/agent-jobs/${id}`, { method: "DELETE" }),
  // Cancelled tasks + jobs whose worktree is still resumable — feeds
  // the "Recently stopped" section on the active agents page.
  getRecoverableAgents: () => request("/agents/recoverable"),
  // Worktree-maintenance read + write. Stats walks every managed
  // worktree dir on disk (cheap-ish); sweep removes those older than
  // max_age_days whose AgentJob is terminal.
  getWorktreeStats: () => request("/agents/worktrees/stats"),
  sweepWorktrees: (maxAgeDays) =>
    request("/agents/worktrees/sweep", {
      method: "POST",
      body: JSON.stringify({ max_age_days: maxAgeDays }),
    }),
  ackAgentJob: (id) => request(`/agent-jobs/${id}/ack`, { method: "POST" }),

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
  approveProposal: (id, draft) => request(`/proposals/${id}/approve`, { method: "POST", body: JSON.stringify(draft ? { draft } : {}) }),
  approveProposalAsGoal: (id) => request(`/proposals/${id}/approve-as-goal`, { method: "POST" }),
  dismissProposal: (id) => request(`/proposals/${id}/dismiss`, { method: "POST" }),

  // Custom themes
  getThemes: () => request("/themes"),
  getTheme: (id) => request(`/themes/${id}`),
  saveTheme: (data) => request("/themes", { method: "POST", body: JSON.stringify(data) }),
  deleteTheme: (id) => request(`/themes/${id}`, { method: "DELETE" }),

  // Home overview — the rolling LLM-generated pane
  getHomeOverview: () => request("/home/overview"),

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
  dismissMemo: (id) => request(`/memos/${id}/dismiss`, { method: "POST" }),
  // Approve a memo. Optional body lets handlers receive extra input
  // (e.g. repo_path for job_approval when no local clone was found).
  // Backend returns 422 with { needs_input, payload } when the
  // handler can't proceed without more input — the caller should
  // surface a prompt and retry.
  approveMemo: (id, body = {}) =>
    request(`/memos/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),
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
  getSystemHealth: () => request("/system/health"),

  // Shutdown / cleanup ritual (power button on the nav)
  getShutdownPreview: () => request("/shutdown/preview"),
  runShutdownStep: (name) =>
    request("/shutdown/step", { method: "POST", body: JSON.stringify({ name }) }),

  // Agent terminal & sessions
  openTerminal: (path, taskId, branch) => request("/agents/open-terminal", { method: "POST", body: JSON.stringify({ path, task_id: taskId, branch }) }),
  resumeAgentSession: (taskId) => request("/agents/resume-session", { method: "POST", body: JSON.stringify({ task_id: taskId }) }),

  // Training / LoRA wrappers retired as part of the lora-park —
  // the backend training_bp + lora_bp are no longer registered. The
  // training pipeline code stays in src/planet_maiko/brain/learning/
  // dormant for future revival.

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
  getJustArrivedProfiles: () => request("/profiles/just-arrived"),
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
