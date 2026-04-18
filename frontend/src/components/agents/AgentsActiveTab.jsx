import { useState } from "react";
import {
  AlertTriangle, Bot, Bone, CheckSquare, Clock, ExternalLink, GitBranch,
  GitPullRequest, HeartPulse, Link2, Loader, MessageCircle, Moon, Play, Sparkles, X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import { formatTime } from "../../utils/dates";
import AgentTimelineModal from "./AgentTimelineModal";

/**
 * Active tab — pack awareness, queued tasks, ready-to-launch agents, live
 * activity, and the channel-log thread modal. Right sidebar shows the
 * leaderboard.
 *
 * Props:
 *   agents     — Agent[] from /api/agents (prepared/ready)
 *   activity   — agent activity entries from /api/agents/activity
 *   queued     — tasks routed to agents but not yet started
 *   conflicts  — conflict warning entries
 *   profiles   — for resolving display names
 *   onRefresh  — () => void; refetch agents/activity/queued after actions
 */
export default function AgentsActiveTab({ agents, activity, queued = [], conflicts, profiles, externalSessions = [], onRefresh }) {
  const [triggeringCycle, setTriggeringCycle] = useState(false);

  const triggerCycle = async () => {
    if (triggeringCycle) return;
    setTriggeringCycle(true);
    showToast("Running brain cycle...", "normal");
    try {
      await api.runBrainCycle();
      showToast("Cycle done — refreshing", "normal");
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
  const [timelineFor, setTimelineFor] = useState(null);

  const openTimeline = (agentId) => {
    if (!agentId) return;
    const profile = profiles.find((p) => p.id === agentId);
    setTimelineFor({
      agentId,
      agentName: profile?.display_name || agentId.replace(/^agent-/, ""),
    });
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
    await api.sendToAgent(selectedThread, { content: msgInput, sender: "user" });
    setMsgInput("");
    setMessages(await api.getAgentMessages(selectedThread));
  };

  const handleLaunch = async (a) => {
    const path = a.working_path || a.extra?.working_path;
    if (path) {
      try {
        await api.openTerminal(path, a.task_id, a.branch);
        showToast("Launching agent...", "normal");
      } catch (err) {
        showToast("Could not open terminal", "high");
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
      showToast(err.message || "Could not re-run agent", "high");
    }
  };

  const handleNudge = async (taskId) => {
    if (!taskId) return;
    try {
      await api.nudgeAgent(taskId);
      showToast("Nudge sent — agent will report in", "normal");
    } catch (err) {
      showToast(err.message || "Could not send nudge", "high");
    }
  };

  const handleResume = async (a) => {
    try {
      await api.resumeAgentSession(a.task_id);
      showToast("Resuming session in terminal", "normal");
    } catch (err) {
      // No session yet — try opening terminal in worktree
      if (a.working_path) {
        try {
          await api.openTerminal(a.working_path, a.task_id, a.branch);
          showToast("Attaching to session...", "normal");
        } catch (e) {
          showToast("Could not open session", "high");
        }
      } else {
        showToast("No session found — agent may not have started yet", "normal");
      }
    }
  };

  return (
    <div className="agents-active-main">
      {/* Pack Awareness — conflict warnings */}
        {conflicts.length > 0 && (
          <div className="pack-awareness card">
            <div className="pack-awareness-header">
              <AlertTriangle size={14} /> Pack Awareness
              <span className="badge high">{conflicts.length} warning(s)</span>
            </div>
            <div className="pack-awareness-list">
              {conflicts.map((c) => {
                const isResolved = c.message_type === "conflict_resolved";
                const isDuplicate = c.message_type === "conflict_directive";
                return (
                  <div key={c.id} className={`conflict-item ${isResolved ? "resolved" : ""}`}>
                    {isResolved
                      ? <span className="conflict-status-icon">✅</span>
                      : isDuplicate
                        ? <span className="conflict-status-icon">⚠️</span>
                        : <AlertTriangle size={12} className="conflict-icon" />}
                    <span className="conflict-task">{c.task_id}</span>
                    <span className="conflict-content">{c.content}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* External sessions: registered by external orchestrators via
            the maiko-brain MCP. These are LLM coding sessions running
            outside Maiko's own worktrees — their work participates in
            the A2A conflict detector but Maiko didn't spawn them. */}
        {externalSessions.length > 0 && (
          <div className="external-sessions-section card">
            <div className="external-sessions-header">
              <Link2 size={14} /> External sessions
              <span className="badge">{externalSessions.length}</span>
            </div>
            <div className="external-sessions-list">
              {externalSessions.map((s) => {
                const regMs = s.registered_at ? Date.parse(s.registered_at) : null;
                const ageMin = regMs ? Math.max(0, Math.round((Date.now() - regMs) / 60000)) : 0;
                return (
                  <div
                    key={s.session_id}
                    className="external-session-item"
                    title={s.worktree_path || ""}
                  >
                    {s.consumer && (
                      <span className="external-session-consumer">{s.consumer}</span>
                    )}
                    <span className="external-session-repo">{s.repo}</span>
                    {s.hint && (
                      <span className="external-session-hint">— {s.hint}</span>
                    )}
                    <span className="external-session-age">
                      {ageMin < 1 ? "just now" : `${ageMin}m ago`}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Queued: tasks routed to an agent but not yet started.
            Without this section, when the brain cycle has assigned
            an agent but the next cycle hasn't prepared the worktree
            yet (e.g. cycle just ran assignment, hasn't yet hit the
            execute phase), the user sees an empty Active tab and
            thinks nothing is happening. */}
        {queued.length > 0 && (
          <div className="queued-section card">
            <div className="queued-header">
              <Clock size={14} /> Queued
              <span className="badge">{queued.length}</span>
              <button
                className="btn btn-sm queued-cycle-btn"
                onClick={triggerCycle}
                disabled={triggeringCycle}
                title="Run a brain cycle now to start these tasks"
              >
                {triggeringCycle
                  ? <><Loader size={10} className="spin" /> Running…</>
                  : <><Sparkles size={10} /> Run cycle now</>}
              </button>
            </div>
            <div className="queued-list">
              {queued.map((q) => (
                <div key={q.task_id} className="queued-item">
                  <span className="queued-type">{q.type}</span>
                  <span className="queued-title" title={q.title}>{q.title}</span>
                  <span className="queued-agent"><Bot size={10} /> {q.agent_name}</span>
                  <span className="queued-time">
                    {q.queued_for_minutes < 1 ? "just now" : `${q.queued_for_minutes}m queued`}
                  </span>
                  {q.url && (
                    <a href={q.url} target="_blank" rel="noreferrer" className="queued-link">
                      <ExternalLink size={10} />
                    </a>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {dormantAgents.length === 0 && activity.length === 0 && queued.length === 0 ? (
          <div className="empty-state">
            <span style={{ fontSize: 48 }}>🐾</span>
            <div className="empty-title">No active agents</div>
            <div className="empty-sub">
              Create a new agent to get started, or
              {" "}
              <button
                className="btn-link"
                onClick={triggerCycle}
                disabled={triggeringCycle}
                style={{ background: "none", border: "none", padding: 0, color: "var(--pink)", cursor: "pointer", textDecoration: "underline" }}
              >
                run a brain cycle now
              </button>
              {" "}to route any waiting tasks.
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
                    ? `Stuck — no pupdates for ${ageMin}m. Try Re-run${isOneShot ? "" : " or open a terminal"}.`
                    : "Starting up — first message hasn't landed yet"}
                  <div className="speech-time">
                    {formatTime(a.prepared_at)}
                  </div>
                </div>
                <div className="agent-card-body">
                  <div className="agent-avatar-circle">
                    <Bot size={18} />
                  </div>
                  <div className="agent-info">
                    <div className="agent-name-row">
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
                  {a.role === "coding" && a.task_id && (
                    <Link
                      to={`/tasks/${a.task_id}/review`}
                      className="btn btn-sm btn-primary"
                      title="Review the agent's changes"
                    >
                      <GitPullRequest size={12} /> Review diff
                    </Link>
                  )}
                  {/* Re-run is the right escape hatch for one-shot
                      agents (review / investigation): re-fires the
                      autonomous skill in the same worktree without
                      needing a terminal. For coding agents, fall
                      back to Relaunch (open a terminal). */}
                  {isOneShot && a.task_id && (
                    <button
                      className="btn btn-sm btn-approve"
                      onClick={() => handleRerun(a.task_id)}
                      title="Re-fire the autonomous run for this review/investigation"
                    >
                      <Sparkles size={12} /> Re-run
                    </button>
                  )}
                  <button
                    className="btn btn-sm"
                    onClick={() => handleResume(a)}
                    title="Attach to the agent's session in a terminal"
                  >
                    <ExternalLink size={12} /> View Session
                  </button>
                  <button className="btn btn-sm" onClick={() => loadThread(a.task_id)}>
                    <MessageCircle size={12} /> Channel Log
                  </button>
                  <button
                    className="btn btn-sm"
                    onClick={() => openTimeline(a.agent_id)}
                    title={`See ${profiles.find((p) => p.id === a.agent_id)?.display_name || "this agent"}'s full activity across all tasks`}
                  >
                    <Clock size={12} /> Timeline
                  </button>
                  {!isOneShot && (
                    <button
                      className="btn btn-sm"
                      onClick={() => handleLaunch(a)}
                      title="Open a terminal in the worktree (use if the auto-start failed)"
                    >
                      <Play size={12} /> Relaunch
                    </button>
                  )}
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
              const displayName = a.agent_name || profile?.display_name || a.task_id.replace(/^(task-|agent-report-|agent-)/, "");

              return (
              // Same per-(agent, task) keying — task_id is the unique
              // identifier for a single workstream. activity has one
              // entry per task already, so this just stops React's
              // index-based key from churning on reorder.
              <div key={a.task_id} className="agent-card card">
                <div className={`speech-bubble status-${a.status}`}>
                  {a.last_message || "No recent messages"}
                  <div className="speech-time">
                    {formatTime(a.last_seen)}
                  </div>
                </div>
                <div className="agent-card-body">
                  <div className="agent-avatar-circle">🐕</div>
                  <div className="agent-info">
                    <div className="agent-name-row">
                      <span className="agent-name">{displayName}</span>
                      <span className={`badge ${a.status}`}>{a.status}</span>
                    </div>
                    {a.task_title && (
                      <div className="agent-task-title" title={a.task_title}>
                        <CheckSquare size={11} /> {a.task_title}
                      </div>
                    )}
                    <div className="agent-chips">
                      <span className="agent-chip"><HeartPulse size={10} /> {a.idle_minutes}m ago</span>
                      <span className="agent-chip">{a.pupdate_count} updates</span>
                      {a.task_type && <span className="agent-chip">{a.task_type}</span>}
                    </div>
                  </div>
                </div>
                <div className="agent-actions">
                  {/* Review diff is available on any active task —
                      gating on prepared?.role was wrong, since
                      list_prepared filters out tasks that are already
                      done and the prepared entry can just be missing
                      (e.g. user dismissed the agent_ready pupdate).
                      The review page itself shows an empty-diff
                      state gracefully if there's no worktree yet. */}
                  {a.task_id && (
                    <Link
                      to={`/tasks/${a.task_id}/review`}
                      className="btn btn-sm btn-primary"
                      title="Review the agent's changes"
                    >
                      <GitPullRequest size={12} /> Review diff
                    </Link>
                  )}
                  <button
                    className="btn btn-sm"
                    onClick={() => handleResume({ task_id: a.task_id, working_path: prepared?.working_path, branch: prepared?.branch })}
                    title="Resume the agent's Claude Code session in a terminal"
                  >
                    <ExternalLink size={12} /> View Session
                  </button>
                  <button className="btn btn-sm" onClick={() => loadThread(a.task_id)}>
                    <MessageCircle size={12} /> Channel Log
                  </button>
                  <button
                    className="btn btn-sm"
                    onClick={() => openTimeline(profileId)}
                    title="See this agent's full activity across all tasks"
                  >
                    <Clock size={12} /> Timeline
                  </button>
                  <button
                    className="btn btn-sm btn-comms"
                    onClick={() => handleNudge(a.task_id)}
                    title="Ping the agent for a status update"
                  >
                    <Bone size={12} /> Nudge
                  </button>
                </div>
              </div>
              );
            })}
          </div>
        )}

        {selectedThread && (
          <div className="modal-overlay" onClick={() => setSelectedThread(null)}>
            <div className="thread-modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <MessageCircle size={14} /> Thread: {selectedThread}
                <span style={{ flex: 1 }} />
                <button className="btn btn-sm modal-close-btn" onClick={() => setSelectedThread(null)}>
                  <X size={14} />
                </button>
              </div>
              <div className="thread-messages">
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
        )}

        {timelineFor && (
          <AgentTimelineModal
            agentId={timelineFor.agentId}
            agentName={timelineFor.agentName}
            onClose={() => setTimelineFor(null)}
          />
        )}
    </div>
  );
}
