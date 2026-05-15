import { useEffect, useState } from "react";
import {
  AlertTriangle, CheckSquare, ChevronDown, ChevronRight, Clock,
  ExternalLink, GitBranch, HeartPulse, Play, RotateCcw, Sparkles, X,
} from "@icons";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import { formatTime, relativeFromMinutes, relativeTime } from "../../utils/dates";
import { formatRepo, useDefaultOrg } from "../../utils/repo";
import CardAvatar from "../CardAvatar";

/**
 * Active tab — pack awareness, ready-to-launch agents, live activity,
 * a queued section (collapsed by default), recent failures with
 * retry/dismiss, and the recoverable cancelled-jobs section. Each
 * agent / job card links to /jobs/<id>; chat used to live inline as
 * a modal but moved to the dedicated job page.
 *
 * Props:
 *   agents     — Agent[] from /api/agents (prepared/ready)
 *   activity   — agent activity entries from /api/agents/activity
 *   conflicts  — conflict warning entries
 *   profiles   — for resolving display names
 *   onRefresh  — () => void; refetch agents/activity after actions
 */
export default function AgentsActiveTab({ agents, activity, conflicts, profiles, onRefresh }) {
  const [triggeringCycle, setTriggeringCycle] = useState(false);
  const defaultOrg = useDefaultOrg();

  const triggerCycle = async () => {
    if (triggeringCycle) return;
    setTriggeringCycle(true);
    showToast("Brain's thinking...", "normal");
    try {
      await api.runBrainCycle();
      showToast("All caught up — refreshing 🌱", "normal");
      onRefresh?.();
    } catch (err) {
      showToast(err.message || "Cycle failed", "high");
    } finally {
      setTriggeringCycle(false);
    }
  };

  // With autonomous agents, every prepared agent also has an active
  // session and an entry in `activity` as soon as it sends its first
  // pupdate. Rendering both `agents` and `activity` would show two
  // cards for the same job — the "Ready to launch" one (stale) and
  // the live one. Dedupe: if a job_id has any activity, skip its
  // prepared-agent card and let the activity entry represent it.
  // list_prepared() now returns job_id; activity entries still use
  // task_id (which post-unification IS the AgentJob.id), so compare
  // both keys.
  const activeIds = new Set(activity.map((a) => a.task_id || a.job_id).filter(Boolean));
  const dormantAgents = agents.filter((a) => {
    const id = a.job_id || a.task_id;
    return !id || !activeIds.has(id);
  });
  // Queue + recent failures — separate fetch so the live grid stays
  // pupdate-driven and this section reflects pure AgentJob state.
  // Split into two: queued goes into its own collapsed section so it
  // doesn't crowd the page; failures stay open with retry/dismiss
  // affordances since they need user attention.
  const [queuedJobs, setQueuedJobs] = useState([]);
  const [failedJobs, setFailedJobs] = useState([]);
  const [queuedOpen, setQueuedOpen] = useState(false);
  const [failedOpen, setFailedOpen] = useState(true);
  // Cancelled tasks/jobs whose worktree is still on disk — revivable.
  // Default-collapsed because this is a recovery surface, not the
  // primary view; the user opens it when they need to undo a misclick.
  const [recoverable, setRecoverable] = useState([]);
  const [recoverableOpen, setRecoverableOpen] = useState(false);

  const reloadJobLists = () => {
    api.getAgentJobs({ status: "queued", limit: 20 })
      .then((rows) => setQueuedJobs(Array.isArray(rows) ? rows : []))
      .catch(() => setQueuedJobs([]));
    api.getAgentJobs({ status: "failed", limit: 20 })
      .then((rows) => setFailedJobs(Array.isArray(rows) ? rows : []))
      .catch(() => setFailedJobs([]));
  };
  useEffect(() => {
    let cancelled = false;
    api.getAgentJobs({ status: "queued", limit: 20 })
      .then((rows) => { if (!cancelled) setQueuedJobs(Array.isArray(rows) ? rows : []); })
      .catch(() => { if (!cancelled) setQueuedJobs([]); });
    api.getAgentJobs({ status: "failed", limit: 20 })
      .then((rows) => { if (!cancelled) setFailedJobs(Array.isArray(rows) ? rows : []); })
      .catch(() => { if (!cancelled) setFailedJobs([]); });
    api.getRecoverableAgents()
      .then((rows) => { if (!cancelled) setRecoverable(Array.isArray(rows) ? rows : []); })
      .catch(() => { if (!cancelled) setRecoverable([]); });
    return () => { cancelled = true; };
  }, [activity.length, agents.length]);

  const handleRevive = async (entry) => {
    const isJob = entry.kind === "job";
    const id = isJob ? entry.job_id : entry.task_id;
    try {
      if (isJob) await api.reviveAgentJob(id);
      else await api.reviveTask(id);
      showToast(`Revived "${entry.task_title || id}" — worktree intact`, "normal");
      onRefresh?.();
      // Also refresh the recoverable list since this entry just left it.
      api.getRecoverableAgents()
        .then((rows) => setRecoverable(Array.isArray(rows) ? rows : []))
        .catch(() => {});
    } catch (err) {
      const msg = err.message || "Couldn't revive";
      showToast(msg, "high");
    }
  };

  const handleForget = async (entry) => {
    const isJob = entry.kind === "job";
    const noun = isJob ? "job" : "task";
    const tail = isJob
      ? "This removes the row and cleans the worktree. There's no undo."
      : "This removes the task, the worktree, and all linked diff comments. There's no undo.";
    const confirmed = window.confirm(
      `Permanently delete "${entry.task_title}"? ${tail}`
    );
    if (!confirmed) return;
    try {
      if (isJob) await api.deleteAgentJob(entry.job_id);
      else await api.forgetTask(entry.task_id);
      showToast(`${noun[0].toUpperCase()}${noun.slice(1)} forgotten — worktree cleaned`, "normal");
      api.getRecoverableAgents()
        .then((rows) => setRecoverable(Array.isArray(rows) ? rows : []))
        .catch(() => {});
    } catch (err) {
      showToast(err.message || "Couldn't forget", "high");
    }
  };

  const handleCancelQueued = async (job) => {
    try {
      await api.cancelAgentJob(job.id);
      showToast(`Cancelled "${job.title || job.id}" before kickoff`, "normal");
      reloadJobLists();
      onRefresh?.();
    } catch (err) {
      showToast(err.message || "Couldn't cancel", "high");
    }
  };

  const handleRetryFailed = async (job) => {
    try {
      await api.retryAgentJob(job.id);
      showToast(`Re-queued "${job.title || job.id}"`, "normal");
      reloadJobLists();
      onRefresh?.();
    } catch (err) {
      showToast(err.message || "Couldn't retry", "high");
    }
  };

  const handleDismissFailed = async (job) => {
    const confirmed = window.confirm(
      `Delete "${job.title || job.id}"? Removes the job row and its worktree. No undo.`
    );
    if (!confirmed) return;
    try {
      await api.deleteAgentJob(job.id);
      showToast("Dismissed", "normal");
      reloadJobLists();
    } catch (err) {
      showToast(err.message || "Couldn't dismiss", "high");
    }
  };

  const handleLaunch = async (a) => {
    const path = a.working_path || a.extra?.working_path;
    if (path) {
      try {
        await api.openTerminal(path, a.task_id, a.branch);
        showToast("On the way...", "normal");
      } catch (err) {
        showToast("Couldn't open the terminal", "high");
      }
    } else {
      showToast(`Checkout branch: git checkout ${a.branch} && claude`, "normal");
    }
  };

  const handleRerun = async (jobId) => {
    if (!jobId) return;
    try {
      await api.rerunAgent(jobId);
      showToast("Re-running agent — first message should land in a moment", "normal");
      onRefresh?.();
    } catch (err) {
      showToast(err.message || "Couldn't re-run", "high");
    }
  };

  const handleStop = async (a) => {
    // Accepts either an activity entry (task or job) or a legacy
    // (taskId, taskTitle) pair from older call sites. The kind
    // discriminator decides which cancel endpoint runs — task vs
    // job — since /tasks/<id>/cancel 404s on a job_id and vice versa.
    //
    // a.task_id is the canonical inbox key (post-unification: the
    // AgentJob.id), so for kind="task" entries we route through
    // a.linked_task_id when present — that's the real Task.id the
    // /tasks/<id>/cancel endpoint wants. Falls back to a.task_id for
    // legacy task-keyed entries that pre-date the unification.
    const isJob = a?.kind === "job";
    const id = isJob
      ? (a.job_id || a.task_id)
      : (a?.linked_task_id || a?.task_id);
    const title = a?.task_title || id;
    if (!id) return;
    const noun = isJob ? "agent job" : "task";
    const confirmed = window.confirm(
      `Stop the agent for "${title}"? This terminates the Claude Code process. The worktree + session stay around — you can revive the ${noun} from the "Recently stopped" section if you change your mind.`
    );
    if (!confirmed) return;
    try {
      const res = isJob
        ? await api.cancelAgentJob(id)
        : await api.cancelTask(id);
      const note = res?.agent_stopped ? " (process terminated)" : " (no active process)";
      showToast(`Stopped${note}`, "normal");
      onRefresh?.();
    } catch (err) {
      showToast(err.message || "Couldn't stop", "high");
    }
  };

  const handleResume = async (a) => {
    try {
      const res = await api.resumeAgentSession(a.task_id);
      // Backend downgrades to tail-only when the agent is mid-run so
      // View Session can't race the headless claude. Tell the user
      // what they're looking at so "is it safe to close?" is obvious.
      const mode = res?.mode;
      if (mode === "tail-busy") {
        showToast("Agent's working — opened a read-only log view. Close anytime 🐾", "normal");
      } else if (mode === "resume") {
        showToast("Chatting with the agent. Close the terminal anytime — the agent keeps running.", "normal");
      } else if (mode === "tmux") {
        showToast("Attached to live tmux session", "normal");
      } else {
        showToast("Opened in terminal", "normal");
      }
    } catch (err) {
      if (a.working_path) {
        try {
          await api.openTerminal(a.working_path, a.task_id, a.branch);
          showToast("Attaching to session...", "normal");
        } catch (e) {
          showToast("Couldn't open the session", "high");
        }
      } else {
        showToast("No session yet — agent's still getting ready", "normal");
      }
    }
  };

  return (
    <div className="agents-active-main">
        {dormantAgents.length === 0 && activity.length === 0 && queuedJobs.length === 0 && failedJobs.length === 0 ? (
          <div className="empty-state">
            <span style={{ fontSize: 48 }}>🐾</span>
            <div className="empty-title">Quiet right now</div>
            <div className="empty-sub">
              Nobody's on anything at the moment. Create a new agent, or
              {" "}
              <button
                className="btn-link"
                onClick={triggerCycle}
                disabled={triggeringCycle}
                style={{ background: "none", border: "none", padding: 0, color: "var(--pink)", cursor: "pointer", textDecoration: "underline" }}
              >
                give the brain a nudge
              </button>
              {" "}to see if there's waiting work.
            </div>
          </div>
        ) : (
          <div className="agent-grid">
            {dormantAgents.map((a) => {
              // If the agent has been "starting up" for more than 5
              // minutes without sending a single pupdate, the
              // headless run almost certainly died silently — don't
              // keep telling the user it's "warming up", say it's
              // stuck and offer the re-run path.
              const preparedAtMs = a.prepared_at ? Date.parse(a.prepared_at) : null;
              const ageMin = preparedAtMs ? Math.round((Date.now() - preparedAtMs) / 60000) : 0;
              const isStuck = ageMin >= 5;
              const isOneShot = a.role === "review" || a.role === "investigation" || a.role === "cartographer";
              // list_prepared was renamed to return job_id/job_title;
              // fall through to task_id/task_title in case the API
              // hasn't redeployed yet.
              const jobLinkId = a.job_id || a.task_id;
              const cardTitle = a.job_title || a.task_title;
              return (
              // key on the job id — same agent profile can be
              // assigned to multiple jobs at once. Keying on agent_id
              // collapsed those into a single card and made siblings
              // disappear into React's reconciler.
              <div key={jobLinkId || a.agent_id} className="agent-card card">
                {jobLinkId ? (
                  <Link to={`/jobs/${jobLinkId}`} className="agent-card-link">
                    <div className="speech-bubble">
                      {isStuck
                        ? `Hasn't checked in for ${ageMin}m. Try Re-run${isOneShot ? "" : " or open a fresh terminal"}.`
                        : "Just settling in — first message hasn't landed yet"}
                      <div className="speech-time">
                        {formatTime(a.prepared_at)}
                      </div>
                    </div>
                    <div className="agent-card-body">
                      <div className="agent-avatar-circle">
                        <CardAvatar agent={profiles.find((p) => p.id === a.agent_id) || a} size={48} />
                      </div>
                      <div className="agent-info">
                        <div className="agent-name-row">
                          <span
                            className={`agent-state-dot state-${profiles.find((p) => p.id === a.agent_id)?.state || "idle"}`}
                            title={`Agent state: ${profiles.find((p) => p.id === a.agent_id)?.state || "idle"}`}
                          />
                          <span className="agent-name">
                            {profiles.find((p) => p.id === a.agent_id)?.display_name || a.agent_id?.replace("agent-", "")}
                          </span>
                          <span className="badge new">warming up</span>
                        </div>
                        {cardTitle && (
                          <div className="agent-task-title">
                            <CheckSquare size={11} /> {cardTitle}
                          </div>
                        )}
                        <div className="agent-chips">
                          <span className="agent-chip"><GitBranch size={10} /> {a.branch}</span>
                          {jobLinkId && <span className="agent-chip">{jobLinkId}</span>}
                        </div>
                      </div>
                    </div>
                  </Link>
                ) : (
                  <div className="agent-card-link agent-card-link-disabled">
                    <div className="agent-card-body">
                      <div className="agent-avatar-circle">
                        <CardAvatar agent={a} size={48} />
                      </div>
                      <div className="agent-info">
                        <div className="agent-name">
                          {a.agent_id?.replace("agent-", "")}
                        </div>
                        <div className="agent-chips">
                          <span className="agent-chip"><GitBranch size={10} /> {a.branch}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
                <div className="agent-actions">
                  {/* Re-run is the right escape hatch for one-shot
                      agents (review / investigation): re-fires the
                      autonomous skill in the same worktree without
                      needing a terminal. For coding agents, fall
                      back to Relaunch (open a terminal). */}
                  {isOneShot && jobLinkId && a.kind !== "job" && (
                    <button
                      className="btn btn-icon"
                      onClick={() => handleRerun(jobLinkId)}
                      title="Re-run the autonomous skill"
                    >
                      <Sparkles size={12} />
                    </button>
                  )}
                  <button
                    className="btn btn-icon"
                    onClick={() => handleResume({ ...a, task_id: jobLinkId })}
                    title="Attach to the agent's session in a terminal"
                  >
                    <ExternalLink size={12} />
                  </button>
                  {!isOneShot && (
                    <button
                      className="btn btn-icon"
                      onClick={() => handleLaunch({ ...a, task_id: jobLinkId })}
                      title="Open a terminal in the worktree"
                    >
                      <Play size={12} />
                    </button>
                  )}
                  <button
                    className="btn btn-icon btn-danger"
                    onClick={() => handleStop({ ...a, task_id: jobLinkId })}
                    title="Stop the agent, clean up the worktree, and delete this job"
                  >
                    <X size={12} />
                  </button>
                </div>
              </div>
              );
            })}

            {activity.map((a) => {
              // Resolve display name: backend now sets agent_name on
              // the activity payload, fall back to the prepared list
              // / profiles for older entries.
              const prepared = agents.find((ag) => (ag.job_id || ag.task_id) === a.task_id);
              const profileId = a.agent_id || prepared?.agent_id;
              const profile = profileId ? profiles.find((p) => p.id === profileId) : null;
              const displayName = a.agent_name || profile?.display_name || a.task_id.replace(/^(task-|agent-report-|agent-|job-)/, "");
              // a.task_id is the AgentJob.id post-unification.
              const jobLinkId = a.job_id || a.task_id;
              return (
              <div key={a.task_id} className="agent-card card">
                <Link to={`/jobs/${jobLinkId}`} className="agent-card-link">
                  <div className={`speech-bubble status-${a.status}`} title={a.last_message_body}>
                    {a.last_message || "Quiet for now — no messages yet"}
                    <div className="speech-time">
                      {formatTime(a.last_seen)}
                    </div>
                  </div>
                  <div className="agent-card-body">
                    <div className="agent-avatar-circle">
                      <CardAvatar agent={profile || prepared} size={48} />
                    </div>
                    <div className="agent-info">
                      <div className="agent-name-row">
                        <span
                          className={`agent-state-dot state-${profile?.state || "idle"}`}
                          title={`Agent state: ${profile?.state || "idle"}`}
                        />
                        <span className="agent-name">{displayName}</span>
                      </div>
                      {a.task_title && (
                        <div className="agent-task-title">
                          <CheckSquare size={11} /> {a.task_title}
                        </div>
                      )}
                      <div className="agent-chips">
                        <span className="agent-chip"><HeartPulse size={10} /> {relativeFromMinutes(a.idle_minutes)}</span>
                        <span className="agent-chip">{a.pupdate_count} updates</span>
                        {a.task_type && <span className="agent-chip">{a.task_type}</span>}
                      </div>
                    </div>
                  </div>
                </Link>
                <div className="agent-actions">
                  <button
                    className="btn btn-icon"
                    onClick={() => handleResume({ task_id: a.task_id, working_path: prepared?.working_path, branch: prepared?.branch })}
                    title="Resume the agent's session in a terminal"
                  >
                    <ExternalLink size={12} />
                  </button>
                  <button
                    className="btn btn-icon btn-danger"
                    onClick={() => handleStop(a)}
                    title="Stop the agent, clean up the worktree, and delete this job"
                  >
                    <X size={12} />
                  </button>
                </div>
              </div>
              );
            })}
          </div>
        )}

        {failedJobs.length > 0 && (
          <div className="agent-queue-section">
            <button
              type="button"
              className="agent-queue-header"
              onClick={() => setFailedOpen((v) => !v)}
            >
              {failedOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              <AlertTriangle size={11} /> Recent failures
              <span className="agent-queue-count">{failedJobs.length}</span>
            </button>
            {failedOpen && (
              <div className="agent-queue-list">
                {failedJobs.map((j) => {
                  const profile = j.agent_profile_id
                    ? profiles.find((p) => p.id === j.agent_profile_id)
                    : null;
                  const ageRef = j.finished_at || j.updated_at;
                  return (
                    <div key={j.id} className={`agent-queue-row status-${j.status}`}>
                      <div className="agent-queue-avatar">
                        {profile
                          ? <CardAvatar agent={profile} size={28} />
                          : <span className="agent-queue-pending-dot">·</span>}
                      </div>
                      <div className="agent-queue-body">
                        <div className="agent-queue-title">
                          <Link to={`/jobs/${j.id}`} className="agent-queue-link">
                            {j.title || `${j.kind} job`}
                          </Link>
                        </div>
                        <div className="agent-queue-meta">
                          <span className="agent-queue-status status-failed">
                            <AlertTriangle size={9} /> failed
                          </span>
                          {profile && <span className="agent-queue-agent">{profile.display_name}</span>}
                          {j.scope_repo && (
                            <span className="agent-queue-repo" title={j.scope_repo}>
                              {formatRepo(j.scope_repo, defaultOrg)}
                            </span>
                          )}
                          <span className="agent-queue-age">{relativeTime(ageRef)}</span>
                        </div>
                        {j.error && (
                          <div className="agent-queue-error" title={j.error}>
                            {j.error.length > 140 ? j.error.slice(0, 138) + "…" : j.error}
                          </div>
                        )}
                      </div>
                      <div className="agent-queue-actions">
                        <button
                          className="btn btn-sm"
                          onClick={() => handleRetryFailed(j)}
                          title="Re-queue this job for the next cycle"
                        >
                          <RotateCcw size={10} /> Retry
                        </button>
                        <button
                          className="btn btn-sm btn-danger"
                          onClick={() => handleDismissFailed(j)}
                          title="Dismiss the failure and clean up the worktree"
                        >
                          <X size={10} />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {queuedJobs.length > 0 && (
          <div className="agent-queue-section">
            <button
              type="button"
              className="agent-queue-header"
              onClick={() => setQueuedOpen((v) => !v)}
            >
              {queuedOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              <Clock size={11} /> Queued
              <span className="agent-queue-count">{queuedJobs.length}</span>
            </button>
            {queuedOpen && (
              <div className="agent-queue-list">
                {queuedJobs.map((j) => {
                  const profile = j.agent_profile_id
                    ? profiles.find((p) => p.id === j.agent_profile_id)
                    : null;
                  return (
                    <div key={j.id} className={`agent-queue-row status-${j.status}`}>
                      <div className="agent-queue-avatar">
                        {profile
                          ? <CardAvatar agent={profile} size={28} />
                          : <span className="agent-queue-pending-dot" title="No agent assigned yet">·</span>}
                      </div>
                      <div className="agent-queue-body">
                        <div className="agent-queue-title">
                          <Link to={`/jobs/${j.id}`} className="agent-queue-link">
                            {j.title || `${j.kind} job`}
                          </Link>
                        </div>
                        <div className="agent-queue-meta">
                          <span className="agent-queue-status status-queued">queued</span>
                          {profile && <span className="agent-queue-agent">{profile.display_name}</span>}
                          {!profile && (
                            <span className="agent-queue-agent agent-queue-spawning">
                              spawning {j.kind} agent…
                            </span>
                          )}
                          {j.scope_repo && (
                            <span className="agent-queue-repo" title={j.scope_repo}>
                              {formatRepo(j.scope_repo, defaultOrg)}
                            </span>
                          )}
                          <span className="agent-queue-age">{relativeTime(j.created_at)}</span>
                        </div>
                      </div>
                      <div className="agent-queue-actions">
                        <button
                          className="btn btn-sm btn-danger"
                          onClick={() => handleCancelQueued(j)}
                          title="Cancel before the cycle picks it up"
                        >
                          <X size={10} /> Cancel
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {recoverable.length > 0 && (
          <div className="agent-queue-section">
            <button
              type="button"
              className="agent-queue-header"
              onClick={() => setRecoverableOpen((v) => !v)}
            >
              {recoverableOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              <HeartPulse size={11} /> Recently stopped — revivable
              <span className="agent-queue-count">{recoverable.length}</span>
            </button>
            {recoverableOpen && (
              <div className="agent-queue-list">
                {recoverable.map((entry) => {
                  const profile = entry.agent_id
                    ? profiles.find((p) => p.id === entry.agent_id)
                    : null;
                  const key = `${entry.kind}-${entry.task_id}`;
                  return (
                    <div key={key} className="agent-queue-row status-cancelled">
                      <div className="agent-queue-avatar">
                        {profile
                          ? <CardAvatar agent={profile} size={28} />
                          : <span className="agent-queue-pending-dot">·</span>}
                      </div>
                      <div className="agent-queue-body">
                        <div className="agent-queue-title">{entry.task_title || entry.task_id}</div>
                        <div className="agent-queue-meta">
                          <span className="agent-queue-status status-cancelled">cancelled</span>
                          {profile && <span className="agent-queue-agent">{profile.display_name}</span>}
                          {entry.task_type && <span className="agent-queue-agent">{entry.task_type}</span>}
                          {entry.stopped_at && (
                            <span className="agent-queue-age">
                              stopped {relativeTime(entry.stopped_at)}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="agent-queue-actions">
                        <button
                          className="btn btn-sm"
                          onClick={() => handleRevive(entry)}
                          title="Resume the agent — worktree + session are still intact"
                        >
                          <Play size={10} /> Revive
                        </button>
                        <button
                          className="btn btn-sm btn-danger"
                          onClick={() => handleForget(entry)}
                          title="Permanently delete (worktree + row). No undo."
                        >
                          <X size={10} />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

    </div>
  );
}
