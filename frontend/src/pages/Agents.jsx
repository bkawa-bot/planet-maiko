import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Bot, MessageCircle, Moon, Archive, Bone,
  AlertTriangle, Link, GitBranch, Folder, CheckSquare,
  HeartPulse, Plus,
} from "lucide-react";
import "./Agents.css";

const STATUS_COLORS = {
  ready: "var(--blue)", connected: "var(--green)", working: "var(--blue)",
  waiting: "var(--lemon)", idle: "var(--lemon)", disconnected: "var(--urgent)",
  completed: "var(--green)", failed: "var(--urgent)", sleeping: "var(--low)",
};

export default function Agents() {
  const [agents, setAgents] = useState([]);
  const [activity, setActivity] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedThread, setSelectedThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [msgInput, setMsgInput] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  const fetchData = async () => {
    try {
      const [a, act] = await Promise.all([api.getAgents(), api.getAgentActivity()]);
      setAgents(a);
      setActivity(act);
    } catch (err) { console.error(err); }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  const loadThread = async (taskId) => {
    setSelectedThread(taskId);
    try { setMessages(await api.getAgentMessages(taskId)); } catch (err) { console.error(err); }
  };

  const sendMsg = async () => {
    if (!msgInput.trim() || !selectedThread) return;
    await api.sendToAgent(selectedThread, { content: msgInput, sender: "user" });
    setMsgInput("");
    setMessages(await api.getAgentMessages(selectedThread));
  };

  if (loading) return <p className="page-empty">Loading...</p>;

  const allItems = [
    ...agents.map((a) => ({ ...a, _type: "prepared" })),
    ...activity.map((a) => ({ ...a, _type: "active" })),
  ];

  return (
    <div className="agents-page">
      <div className="agents-header">
        <Bot size={20} />
        <h2>Conductor Agents</h2>
        <button className="btn" onClick={() => setShowCreate(!showCreate)} style={{ marginLeft: "auto" }}>
          <Plus size={12} /> New Agent
        </button>
      </div>

      {allItems.length === 0 && !showCreate ? (
        <div className="empty-state">
          <Bot size={48} className="empty-icon" />
          <div className="empty-title">No conductor agents</div>
          <div className="empty-sub">Create one above or launch via Conductor</div>
        </div>
      ) : (
        <div className="agent-grid">
          {/* Prepared agents */}
          {agents.map((a) => (
            <div key={a.agent_id} className="agent-card">
              {/* Speech bubble */}
              <div className="speech-bubble">
                Ready to launch
                <div className="speech-time">{a.prepared_at ? new Date(a.prepared_at).toLocaleTimeString() : ""}</div>
              </div>

              <div className="agent-card-body">
                <div className="agent-avatar" style={{ borderColor: STATUS_COLORS.ready }}>
                  <Bot size={18} />
                  <span className="agent-status-dot" style={{ background: STATUS_COLORS.ready }} />
                </div>

                <div className="agent-info">
                  <div className="agent-name-row">
                    <span className="agent-name">{a.agent_id?.replace("agent-", "")}</span>
                    <span className="badge new">ready</span>
                  </div>
                  <div className="agent-chips">
                    <span className="agent-chip"><GitBranch size={10} /> {a.branch}</span>
                    {a.task_id && <span className="agent-chip"><CheckSquare size={10} /> {a.task_id}</span>}
                  </div>
                </div>
              </div>

              <div className="agent-actions">
                <button className="btn" onClick={() => loadThread(a.task_id)}>
                  <MessageCircle size={12} /> Messages
                </button>
              </div>
            </div>
          ))}

          {/* Active agents from activity */}
          {activity.map((a, i) => (
            <div key={i} className="agent-card">
              <div className={`speech-bubble status-${a.status}`}>
                {a.last_message || "No recent messages"}
                <div className="speech-time">{a.last_seen ? new Date(a.last_seen).toLocaleTimeString() : ""}</div>
              </div>

              <div className="agent-card-body">
                <div className="agent-avatar" style={{ borderColor: STATUS_COLORS[a.status] || STATUS_COLORS.idle }}>
                  <Bot size={18} />
                  <span className="agent-status-dot" style={{ background: STATUS_COLORS[a.status] || STATUS_COLORS.idle }} />
                </div>

                <div className="agent-info">
                  <div className="agent-name-row">
                    <span className="agent-name">{a.task_id}</span>
                    <span className={`badge ${a.status}`}>{a.status}</span>
                  </div>
                  {a.last_message_body && (
                    <div className="agent-body-preview">{a.last_message_body.slice(0, 80)}</div>
                  )}
                  <div className="agent-chips">
                    <span className="agent-chip"><HeartPulse size={10} /> {a.idle_minutes}m ago</span>
                    <span className="agent-chip">{a.pupdate_count} updates</span>
                  </div>
                </div>
              </div>

              <div className="agent-actions">
                <button className="btn"><Bone size={12} /> Nudge</button>
                <button className="btn" onClick={() => loadThread(a.task_id)}>
                  <MessageCircle size={12} /> Messages
                </button>
                <button className="btn"><Moon size={12} /> Sleep</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Message thread */}
      {selectedThread && (
        <div className="thread-panel">
          <div className="thread-header">
            <MessageCircle size={14} /> Thread: {selectedThread}
            <button className="btn btn-sm" onClick={() => setSelectedThread(null)} style={{ marginLeft: "auto" }}>Close</button>
          </div>
          <div className="thread-messages">
            {messages.length === 0 ? (
              <p className="page-empty" style={{ marginTop: 20 }}>No messages yet.</p>
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
      )}
    </div>
  );
}
