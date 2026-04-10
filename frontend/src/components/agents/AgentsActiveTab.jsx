import { useState } from "react";
import {
  AlertTriangle, Bot, Bone, CheckSquare, ExternalLink, GitBranch,
  HeartPulse, MessageCircle, Moon, Play, X,
} from "lucide-react";
import { api } from "../../api/client";
import { showToast } from "../Toast";
import LeaderboardWidget from "../LeaderboardWidget";

/**
 * Active tab — pack awareness, ready-to-launch agents, live activity, and
 * the channel-log thread modal. Right sidebar shows the leaderboard.
 *
 * Props:
 *   agents     — Agent[] from /api/agents (prepared/ready)
 *   activity   — agent activity entries from /api/agents/activity
 *   conflicts  — conflict warning entries
 *   profiles   — for resolving display names
 */
export default function AgentsActiveTab({ agents, activity, conflicts, profiles }) {
  const [selectedThread, setSelectedThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [msgInput, setMsgInput] = useState("");

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
    <div className="agents-active-layout">
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

        {agents.length === 0 && activity.length === 0 ? (
          <div className="empty-state">
            <span style={{ fontSize: 48 }}>🐾</span>
            <div className="empty-title">No active agents</div>
            <div className="empty-sub">Create a new agent to get started. They'll arrive in town ready to help!</div>
          </div>
        ) : (
          <div className="agent-grid">
            {agents.map((a) => (
              <div key={a.agent_id} className="agent-card card">
                <div className="speech-bubble">
                  Ready to launch
                  <div className="speech-time">
                    {a.prepared_at ? new Date(a.prepared_at).toLocaleTimeString() : ""}
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
                      <span className="badge new">ready</span>
                    </div>
                    <div className="agent-chips">
                      <span className="agent-chip"><GitBranch size={10} /> {a.branch}</span>
                      {a.task_id && <span className="agent-chip"><CheckSquare size={10} /> {a.task_id}</span>}
                    </div>
                  </div>
                </div>
                <div className="agent-actions">
                  <button
                    className="btn btn-sm btn-approve"
                    onClick={() => handleLaunch(a)}
                    title="Open a terminal to launch the agent"
                  >
                    <Play size={12} /> Launch
                  </button>
                  <button
                    className="btn btn-sm"
                    onClick={() => handleResume(a)}
                    title="Resume the agent's Claude Code session in a terminal"
                  >
                    <ExternalLink size={12} /> View Session
                  </button>
                  <button className="btn btn-sm" onClick={() => loadThread(a.task_id)}>
                    <MessageCircle size={12} /> Channel Log
                  </button>
                </div>
              </div>
            ))}

            {activity.map((a, i) => (
              <div key={i} className="agent-card card">
                <div className={`speech-bubble status-${a.status}`}>
                  {a.last_message || "No recent messages"}
                  <div className="speech-time">
                    {a.last_seen ? new Date(a.last_seen).toLocaleTimeString() : ""}
                  </div>
                </div>
                <div className="agent-card-body">
                  <div className="agent-avatar-circle">🐕</div>
                  <div className="agent-info">
                    <div className="agent-name-row">
                      <span className="agent-name">{a.task_id}</span>
                      <span className={`badge ${a.status}`}>{a.status}</span>
                    </div>
                    <div className="agent-chips">
                      <span className="agent-chip"><HeartPulse size={10} /> {a.idle_minutes}m ago</span>
                      <span className="agent-chip">{a.pupdate_count} updates</span>
                    </div>
                  </div>
                </div>
                <div className="agent-actions">
                  <button className="btn btn-sm btn-comms"><Bone size={12} /> Nudge</button>
                  <button className="btn btn-sm" onClick={() => loadThread(a.task_id)}>
                    <MessageCircle size={12} /> Channel Log
                  </button>
                  <button className="btn btn-sm"><Moon size={12} /> Sleep</button>
                </div>
              </div>
            ))}
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
                      <span className="thread-msg-time">{new Date(m.created_at).toLocaleTimeString()}</span>
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
      </div>

      <div className="agents-active-sidebar">
        <LeaderboardWidget />
      </div>
    </div>
  );
}
