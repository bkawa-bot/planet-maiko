import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle, CheckSquare, ChevronDown, ChevronRight, Clock,
  ExternalLink, GitBranch, HeartPulse, MessageCircle, Play, Sparkles, X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import { formatTime, relativeFromMinutes, relativeTime } from "../../utils/dates";
import { formatRepo, useDefaultOrg } from "../../utils/repo";
import CardAvatar from "../CardAvatar";
import ModalPortal from "../ModalPortal";

/**
 * Active tab — pack awareness, queued tasks, ready-to-launch agents, live
 * activity, and the channel-log thread modal.
 *
 * Props:
 *   agents     — Agent[] from /api/agents (prepared/ready)
 *   activity   — agent activity entries from /api/agents/activity
 *   queued     — tasks routed to agents but not yet started
 *   conflicts  — conflict warning entries
 *   profiles   — for resolving display names
 *   onRefresh  — () => void; refetch agents/activity/queued after actions
 */
export default function AgentsActiveTab({ agents, activity, queued = [], conflicts, profiles, onRefresh }) {
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
  // cards for the same task — the "Ready to launch" one (stale) and
  // the live one. Dedupe: if a task_id has any activity, skip its
  // prepared-agent card and let the activity entry represent it.
  const activeTaskIds = new Set(activity.map((a) => a.task_id).filter(Boolean));
  const dormantAgents = agents.filter((a) => !a.task_id || !activeTaskIds.has(a.task_id));
  const [selectedThread, setSelectedThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [msgInput, setMsgInput] = useState("");
  const threadMessagesRef = useRef(null);
  // Snap the scroll to the bottom whenever the message list changes
  // — opening the thread or sending a new message — so the user lands
  // on the latest reply instead of the start of the conversation.
  useEffect(() => {
    if (!selectedThread) return;
    const el = threadMessagesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [selectedThread, messages.length]);
  // Queue + recent failures — separate fetch so the live grid stays
  // pupdate-driven and this section reflects pure AgentJob state.
  // Pulled on mount + whenever onRefresh fires upstream (the parent
  // already refetches on relevant actions; we piggyback by keying the
  // effect on activity to rerun when something changes).
  const [queueJobs, setQueueJobs] = useState([]);
  const [queueOpen, setQueueOpen] = useState(true);
  // Cancelled tasks/jobs whose worktree is still on disk — revivable.
  // Default-collapsed because this is a recovery surface, not the
  // primary view; the user opens it when they need to undo a misclick.
  const [recoverable, setRecoverable] = useState([]);
  const [recoverableOpen, setRecoverableOpen] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api.getAgentJobs({ status: "queued,failed", limit: 20 })
      .then((rows) => { if (!cancelled) setQueueJobs(Array.isArray(rows) ? rows : []); })
      .catch(() => { if (!cancelled) setQueueJobs([]); });
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

  const loadThread = async (taskId) => {
    setSelectedThread(taskId);
    try {
      setMessages(await api.getAgentMessages(taskId));
    } catch (err) {
      console.error(err);
    }
  };

  const sendMsg = async () => {
    if (!msgInput.trim() || !selectedThread) return;
    try {
      const res = await api.sendToAgent(selectedThread, {
        content: msgInput,
        sender: "user",
      });
      // Backend auto-wakes when sender=user and returns wake_mode so we
      // can tell the user whether the agent was woken, queued behind a
      // current run, or has no session yet.
      const mode = res?.wake_mode;
      if (mode === "woke") showToast("Message sent — waking the agent ✨", "normal");
      else if (mode === "queued") showToast("Agent's working — queued for the next turn", "normal");
      else if (mode === "dropped") showToast("Sent", "normal");
      else showToast("Message saved to inbox", "normal");
    } catch (err) {
      showToast(err.message || "Couldn't send", "high");
    }
    setMsgInput("");
    setMessages(await api.getAgentMessages(selectedThread));
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

  const handleRerun = async (taskId) => {
    if (!taskId) return;
    try {
      await api.rerunAgent(taskId);
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
        {dormantAgents.length === 0 && activity.length === 0 && queueJobs.length === 0 ? (
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
              return (
              // key on task_id, not agent_id — the same agent profile
              // can be assigned to multiple tasks at once. Keying on
              // agent_id collapsed those into a single card and made
              // siblings disappear into React's reconciler.
              <div key={a.task_id || a.agent_id} className="agent-card card">
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
                    <CardAvatar agent={profiles.find((p) => p.id === a.agent_id) || a} size={40} />
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
                    {a.task_title && (
                      <div className="agent-task-title" title={a.task_title}>
                        <CheckSquare size={11} /> {a.task_title}
                      </div>
                    )}
                    <div className="agent-chips">
                      <span className="agent-chip"><GitBranch size={10} /> {a.branch}</span>
                      {a.task_id && <span className="agent-chip">{a.task_id}</span>}
                    </div>
                  </div>
                </div>
                <div className="agent-actions">
                  {/* Primary action: Chat. Everything else is an icon. */}
                  <button
                    className="btn btn-sm btn-primary"
                    onClick={() => loadThread(a.task_id)}
                    title="Chat with the agent"
                  >
                    <MessageCircle size={12} /> Chat
                  </button>
                  {/* Re-run is the right escape hatch for one-shot
                      agents (review / investigation): re-fires the
                      autonomous skill in the same worktree without
                      needing a terminal. For coding agents, fall
                      back to Relaunch (open a terminal). */}
                  {isOneShot && a.task_id && a.kind !== "job" && (
                    <button
                      className="btn btn-icon"
                      onClick={() => handleRerun(a.linked_task_id || a.task_id)}
                      title="Re-run the autonomous skill"
                    >
                      <Sparkles size={12} />
                    </button>
                  )}
                  <button
                    className="btn btn-icon"
                    onClick={() => handleResume(a)}
                    title="Attach to the agent's session in a terminal"
                  >
                    <ExternalLink size={12} />
                  </button>
                  {!isOneShot && (
                    <button
                      className="btn btn-icon"
                      onClick={() => handleLaunch(a)}
                      title="Open a terminal in the worktree"
                    >
                      <Play size={12} />
                    </button>
                  )}
                  <button
                    className="btn btn-icon btn-danger"
                    onClick={() => handleStop(a)}
                    title="Stop the agent, clean up the worktree, and delete this task"
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
              const prepared = agents.find((ag) => ag.task_id === a.task_id);
              const profileId = a.agent_id || prepared?.agent_id;
              const profile = profileId ? profiles.find((p) => p.id === profileId) : null;
              const displayName = a.agent_name || profile?.display_name || a.task_id.replace(/^(task-|agent-report-|agent-|job-)/, "");

              return (
              // Same per-(agent, task) keying — task_id is the unique
              // identifier for a single workstream. activity has one
              // entry per task already, so this just stops React's
              // index-based key from churning on reorder.
              <div key={a.task_id} className="agent-card card">
                <button
                  type="button"
                  className={`speech-bubble status-${a.status}`}
                  onClick={() => loadThread(a.task_id)}
                  title={a.last_message_body || "Open chat thread"}
                >
                  {a.last_message || "Quiet for now — no messages yet"}
                  <div className="speech-time">
                    {formatTime(a.last_seen)}
                  </div>
                </button>
                <div className="agent-card-body">
                  <div className="agent-avatar-circle">
                    <CardAvatar agent={profile || prepared} size={40} />
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
                      <div className="agent-task-title" title={a.task_title}>
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
                <div className="agent-actions">
                  {/* Primary action on an active card is Chat — the
                      review diff lives on Home and the Task so it
                      doesn't need to duplicate here. Session attach
                      and Stop stay as icon-only escape hatches. */}
                  <button
                    className="btn btn-sm btn-primary"
                    onClick={() => loadThread(a.task_id)}
                    title="Chat with the agent"
                  >
                    <MessageCircle size={12} /> Chat
                  </button>
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
                    title="Stop the agent, clean up the worktree, and delete this task"
                  >
                    <X size={12} />
                  </button>
                </div>
              </div>
              );
            })}
          </div>
        )}

        {queueJobs.length > 0 && (
          <div className="agent-queue-section">
            <button
              type="button"
              className="agent-queue-header"
              onClick={() => setQueueOpen((v) => !v)}
            >
              {queueOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              <Clock size={11} /> Queue + recent failures
              <span className="agent-queue-count">{queueJobs.length}</span>
            </button>
            {queueOpen && (
              <div className="agent-queue-list">
                {queueJobs.map((j) => {
                  const profile = j.agent_profile_id
                    ? profiles.find((p) => p.id === j.agent_profile_id)
                    : null;
                  const ageRef = j.status === "failed"
                    ? (j.finished_at || j.updated_at)
                    : (j.created_at);
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
                          <span className={`agent-queue-status status-${j.status}`}>
                            {j.status === "failed" && <AlertTriangle size={9} />}
                            {j.status}
                          </span>
                          {profile && <span className="agent-queue-agent">{profile.display_name}</span>}
                          {!profile && j.status === "queued" && (
                            <span className="agent-queue-agent agent-queue-spawning">
                              spawning {j.kind} agent…
                            </span>
                          )}
                          {j.scope_repo && (
                            <span className="agent-queue-repo" title={j.scope_repo}>
                              {formatRepo(j.scope_repo, defaultOrg)}
                            </span>
                          )}
                          <span className="agent-queue-age">{relativeTime(ageRef)}</span>
                        </div>
                        {j.status === "failed" && j.error && (
                          <div className="agent-queue-error" title={j.error}>
                            {j.error.length > 140 ? j.error.slice(0, 138) + "…" : j.error}
                          </div>
                        )}
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

        {selectedThread && (() => {
          // Compose a friendly thread title: prefer "<agent name> · <task title>"
          // over the raw bucket key. activity is the canonical source for the
          // currently-displayed agent metadata; queueJobs covers fresh
          // queued jobs that haven't pupdate'd yet; profiles is the fallback
          // for resolving a profile_id off any of those.
          const a = activity.find((x) => x.task_id === selectedThread);
          const j = queueJobs.find((q) => q.id === selectedThread || q.source_task_id === selectedThread);
          const profileId = a?.agent_id || j?.agent_profile_id;
          const profile = profileId ? profiles.find((p) => p.id === profileId) : null;
          const agentName = a?.agent_name || profile?.display_name;
          const taskTitle = a?.task_title || j?.title;
          const titleParts = [];
          if (agentName) titleParts.push(agentName);
          if (taskTitle) titleParts.push(taskTitle);
          const title = titleParts.length ? titleParts.join(" · ") : "Chat";
          return (
          <ModalPortal>
          <div className="modal-overlay" onClick={() => setSelectedThread(null)}>
            <div className="thread-modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <MessageCircle size={14} /> {title}
                <span style={{ flex: 1 }} />
                <button className="btn btn-sm modal-close-btn" onClick={() => setSelectedThread(null)}>
                  <X size={14} />
                </button>
              </div>
              <div className="thread-messages" ref={threadMessagesRef}>
                {messages.length === 0 ? (
                  <p className="page-empty thread-empty">No messages yet.</p>
                ) : messages.map((m) => (
                  <div key={m.id} className={`thread-msg ${m.direction}`}>
                    <div className="thread-msg-header">
                      <span className="thread-msg-sender">{m.sender}</span>
                      <span className="badge">{m.message_type}</span>
                      <span className="thread-msg-time">{formatTime(m.created_at)}</span>
                    </div>
                    <div className="thread-msg-content">{m.content}</div>
                  </div>
                ))}
              </div>
              <div className="thread-input">
                <input
                  value={msgInput}
                  onChange={(e) => setMsgInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && sendMsg()}
                  placeholder="Send message to agent..."
                />
                <button className="btn btn-primary" onClick={sendMsg}>Send</button>
              </div>
            </div>
          </div>
          </ModalPortal>
          );
        })()}

    </div>
  );
}
