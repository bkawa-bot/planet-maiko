/**
 * Automation form-builder schemas — source of truth for the
 * AutomationEditor modal. Adding a new condition/action kind means
 * registering its backend handler in brain/automations/{conditions,
 * actions}.py AND adding an entry here so the form-builder knows
 * which fields to render for it.
 *
 * The DynamicFields renderer reads these dicts to know what inputs
 * to show; the editor itself never has to know about kind-specific
 * field names.
 */

const PUPDATE_TYPE_OPTIONS = [
  "pr_review_requested", "pr_changes_requested", "pr_approved", "pr_merged",
  "pr_ci_passed", "pr_ci_failed", "pr_review_commented",
  "linear_assigned", "linear_mention", "linear_status_changed",
  "calendar_event", "calendar_1on1",
  "agent_ready_for_review", "agent_plan_for_approval", "agent_stuck",
  "agent_proposal", "incident", "error_spike", "deploy_rollback",
  "deploy_blocked", "deploy_stuck", "batch_job_failing",
];

const PRIORITY_OPTIONS = [
  { value: "low", label: "low" },
  { value: "normal", label: "normal" },
  { value: "high", label: "high" },
  { value: "urgent", label: "urgent" },
];

// Task types the USER owns (Tasks page). Separate from agent-job kinds.
const TASK_TYPE_OPTIONS = [
  { value: "todo", label: "todo (generic)" },
  { value: "bug", label: "bug" },
  { value: "feature", label: "feature" },
  { value: "coding", label: "coding (you'll assign an agent later)" },
  { value: "review", label: "review (you owe someone a review)" },
];

const CONDITION_SCHEMAS = {
  cadence: {
    label: "On a schedule",
    group: "Time",
    scopes: ["cycle"],
    help: "Fires every N minutes. Uses last_fired_at + interval as the clock.",
    fields: [
      { name: "interval_minutes", type: "number", label: "Every N minutes", default: 60, min: 1 },
    ],
  },
  overview_stale: {
    label: "A repo's overview goes stale",
    group: "Coverage state",
    scopes: ["cycle"],
    help: "Fires when a repo's cartographer insight is missing or older than the threshold.",
    fields: [
      { name: "repo", type: "string", label: "Repo (org/name)", placeholder: "org/repo", help: "required", datalist: "repos" },
      { name: "stale_days", type: "number", label: "Days before stale", default: 30, min: 1 },
    ],
  },
  pupdate_chain: {
    label: "A chain of pupdate types lands together",
    group: "Events",
    scopes: ["cycle"],
    help: "Fires when all the listed pupdate types appear within the time window, grouped by the same key (usually repo).",
    fields: [
      { name: "types", type: "list", label: "Pupdate types (all required)", placeholder: "pr_ci_failed, error_spike", help: "comma-separated" },
      { name: "within_minutes", type: "number", label: "Window (minutes)", default: 30, min: 1 },
      { name: "group_by", type: "select", label: "Group by", default: "repo", options: ["repo", "tag"], advanced: true },
    ],
  },
  pupdate_match: {
    label: "A pupdate of a specific type comes in",
    group: "Events",
    scopes: ["cycle", "pupdate"],
    help: "Picks up individual incoming pupdates. Type is the most common filter; the other fields narrow further when you need it.",
    fields: [
      { name: "type", type: "select", label: "Type", options: PUPDATE_TYPE_OPTIONS, help: "the pupdate's type field" },
      { name: "title_contains", type: "string", label: "Title contains", placeholder: "substring, case-insensitive", help: "optional extra filter on the title" },
      // Everything below is rare — collapsed under Advanced.
      { name: "source", type: "string", label: "Source", placeholder: "github, linear, calendar…", advanced: true, datalist: "sources", help: "poller name (auto-suggested from your configured pollers)" },
      { name: "type_prefix", type: "string", label: "Type prefix", placeholder: "pr_", advanced: true, help: "match a family of types (e.g. pr_*)" },
      { name: "priority", type: "select", label: "Priority", options: PRIORITY_OPTIONS, advanced: true },
      { name: "actionable", type: "bool", label: "Must be actionable", advanced: true },
      { name: "has_tag", type: "string", label: "Has tag", placeholder: "e.g. ci", advanced: true },
      { name: "within_minutes", type: "number", label: "Window (minutes, cycle-scope only)", help: "how far back to scan in cycle scope; default 60", advanced: true },
    ],
  },
};

const ACTION_SCHEMAS = {
  run_agent_job: {
    label: "Run an agent job (pack-owned)",
    group: "Do work",
    scopes: ["cycle"],
    help: "Spawn an agent to do a one-shot task — cartograph a repo, investigate an incident, run a scheduled skill. Pack-owned: lands on the Pack page, not the Tasks list.",
    fields: [
      { name: "kind", type: "select", label: "Kind", default: "cartograph", optionsKey: "agent_job_kinds" },
      { name: "ask_first", type: "bool", label: "Ask me before running", help: "when on, the job waits for your approval; off runs it directly." },
      { name: "title", type: "string", label: "Title", placeholder: "Can template {service} etc." },
      { name: "description", type: "textarea", label: "Description / input", rows: 2, help: "skill input / what the agent should focus on" },
      { name: "scope_repo", type: "string", label: "Repo", placeholder: "org/repo or {service}", advanced: true, datalist: "repos" },
      { name: "specialty_id", type: "select", label: "Specialty", optionsKey: "specialties", advanced: true, help: "Extra context layered onto the agent's role. Silently dropped if the resolved agent doesn't have it attached." },
      { name: "priority", type: "select", label: "Priority", default: "normal", options: PRIORITY_OPTIONS, advanced: true },
    ],
  },
  notify_me: {
    label: "Notify me",
    group: "Let me know",
    scopes: ["cycle", "pupdate"],
    help: "Drops a notification on the Home page. Use when you just want to be told something happened, no task or agent spawn. Dismissable.",
    fields: [
      { name: "title", type: "string", label: "Title", placeholder: "e.g. 'CI has been red for 30 min' or '{pupdate_title}'", help: "Defaults to the triggering pupdate's title. Supports tokens like {pupdate_title}, {repo}." },
      { name: "body", type: "textarea", label: "Body", rows: 2, help: "Optional extra detail. Markdown. Supports {pupdate_body}, {pupdate_url}, {repo}." },
      { name: "priority", type: "select", label: "Priority", default: "normal", options: PRIORITY_OPTIONS, advanced: true },
      { name: "url", type: "string", label: "Click-through URL", placeholder: "https:// or {pupdate_url}", advanced: true },
    ],
  },
  create_task: {
    label: "Create a task (user-owed)",
    group: "Do work",
    scopes: ["cycle"],
    help: "Create a task you own — a todo / bug / feature that lives on the Tasks page. Use this when the work surfaces to you, not the pack.",
    fields: [
      { name: "title", type: "string", label: "Title" },
      { name: "type", type: "select", label: "Task type", default: "todo", options: TASK_TYPE_OPTIONS },
      { name: "description", type: "textarea", label: "Description", rows: 2 },
      { name: "auto_launch", type: "bool", label: "Launch an agent immediately", help: "For review/investigation/cartograph/repo_analysis types: skip manual Assign and spawn a linked agent job. No-op on todo/bug/feature." },
      { name: "repo", type: "string", label: "Repo", placeholder: "org/repo", advanced: true, datalist: "repos" },
      { name: "priority", type: "select", label: "Priority", default: "normal", options: PRIORITY_OPTIONS, advanced: true },
    ],
  },
  dismiss_pupdate: {
    label: "Dismiss it (archive)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Archives the pupdate. Pure noise-reduction.",
    fields: [],
  },
  create_task_from_pupdate: {
    label: "Create a task from it (user-owed)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Uses the pupdate's title/priority as the task seed. Lands on the Tasks page as work you own.",
    fields: [
      { name: "task_type", type: "select", label: "Task type", default: "todo", options: TASK_TYPE_OPTIONS },
      { name: "task_priority", type: "select", label: "Task priority", options: PRIORITY_OPTIONS, advanced: true },
    ],
  },
  spawn_agent_job_from_pupdate: {
    label: "Spawn an agent job from it (pack-owned)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Pack handles this pupdate — e.g. incident → investigate. Job uses the pupdate's repo and title as context.",
    fields: [
      { name: "kind", type: "select", label: "Job kind", default: "investigation", optionsKey: "agent_job_kinds" },
      { name: "ask_first", type: "bool", label: "Ask me before running" },
      { name: "title", type: "string", label: "Title override (optional)", advanced: true },
      { name: "description", type: "textarea", label: "Description override (optional)", rows: 2, advanced: true },
      { name: "specialty_id", type: "select", label: "Specialty", optionsKey: "specialties", advanced: true, help: "Extra context layered onto the agent's role. Silently dropped if the resolved agent doesn't have it attached." },
      { name: "priority", type: "select", label: "Priority", options: PRIORITY_OPTIONS, advanced: true },
    ],
  },
  complete_linked_task: {
    label: "Close the linked task (PR merged / approved)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Closes tasks whose url matches the pupdate's url. Cleans up worktrees for Maiko-owned coding tasks.",
    fields: [],
  },
  skip: {
    label: "Skip it (acknowledge, no action)",
    group: "Handle the pupdate",
    scopes: ["pupdate"],
    help: "Marks the pupdate processed without dispatching anything. Useful for 'ignore this pattern' without deleting the automation.",
    fields: [],
  },
};


export { PUPDATE_TYPE_OPTIONS, PRIORITY_OPTIONS, TASK_TYPE_OPTIONS, CONDITION_SCHEMAS, ACTION_SCHEMAS };
