import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Bot, MessageCircle, Moon, Bone, GitBranch, CheckSquare,
  HeartPulse, Plus, Star, Trophy, X, ChevronRight, AlertTriangle,
} from "lucide-react";
import "./Agents.css";

const AVATAR_EMOJI = {
  shiba: "🐕", corgi: "🐶", husky: "🐺", poodle: "🐩", golden: "🦮", beagle: "🐕‍🦺",
  dalmatian: "🐾", samoyed: "☁️", akita: "🐕", pomeranian: "🧸",
  calico_cat: "🐱", tabby_cat: "🐈", black_cat: "🐈‍⬛",
  bunny: "🐰", hamster: "🐹", fox: "🦊",
};

const RANK_LABELS = { pup: "🌱 Pup", junior: "⭐ Junior", senior: "🌟 Senior", expert: "👑 Expert" };

export default function Agents() {
  const [tab, setTab] = useState("active");
  const [profiles, setProfiles] = useState([]);
  const [agents, setAgents] = useState([]);
  const [activity, setActivity] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedThread, setSelectedThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [msgInput, setMsgInput] = useState("");
  const [showArrival, setShowArrival] = useState(null);
  const [conflicts, setConflicts] = useState([]);

  const fetchData = async () => {
    try {
      const [p, a, act, conf] = await Promise.all([
        api.getProfiles(),
        api.getAgents(),
        api.getAgentActivity(),
        api.getConflicts().catch(() => []),
      ]);
      setProfiles(p);
      setAgents(a);
      setActivity(act);
      setConflicts(conf);
    } catch (err) { console.error(err); }
    setLoading(false);
  };

  useEffect(() => { fetchData(); }, []);

  const handleCreateAgent = async () => {
    try {
      const profile = await api.createProfile({});
      setShowArrival(profile);
      fetchData();
    } catch (err) { console.error(err); }
  };

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

  return (
    <div className="agents-page">
      {/* Arrival Modal */}
      {showArrival && (
        <div className="modal-overlay" onClick={() => setShowArrival(null)}>
          <div className="modal arrival-modal" onClick={(e) => e.stopPropagation()}>
            <div className="arrival-content">
              <div className="arrival-avatar">{AVATAR_EMOJI[showArrival.avatar] || "🐕"}</div>
              <h2 className="arrival-greeting">Hello! My name is {showArrival.display_name}!</h2>
              <p className="arrival-flavor">{showArrival.flavor_text}</p>
              <p className="arrival-intro">I'm new to town and excited to help out! Assign me a task and let's get to work together.</p>
              <div className="arrival-rank">{RANK_LABELS[showArrival.rank] || "🌱 Pup"}</div>
              <button className="btn btn-primary" onClick={() => setShowArrival(null)}>
                Welcome, {showArrival.display_name}!
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="agents-header">
        <Bot size={20} />
        <h2>Conductor Agents</h2>
        <div className="agents-tabs" style={{ marginLeft: "auto" }}>
          <button className={`inbox-tab ${tab === "active" ? "active" : ""}`} onClick={() => setTab("active")}>Active</button>
          <button className={`inbox-tab ${tab === "profiles" ? "active" : ""}`} onClick={() => setTab("profiles")}>
            Profiles {profiles.length > 0 && <span className="tab-badge">{profiles.length}</span>}
          </button>
        </div>
        <button className="btn btn-primary" onClick={handleCreateAgent}>
          <Plus size={12} /> New Agent
        </button>
      </div>

      {tab === "active" && (
        <>
          {/* Pack Awareness — conflict warnings */}
          {conflicts.length > 0 && (
            <div className="pack-awareness card">
              <div className="pack-awareness-header">
                <AlertTriangle size={14} /> Pack Awareness
                <span className="badge high">{conflicts.length} warning(s)</span>
              </div>
              <div className="pack-awareness-list">
                {conflicts.map((c) => (
                  <div key={c.id} className="conflict-item">
                    <AlertTriangle size={12} className="conflict-icon" />
                    <span className="conflict-task">{c.task_id}</span>
                    <span className="conflict-content">{c.content}</span>
                  </div>
                ))}
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
                    <div className="speech-time">{a.prepared_at ? new Date(a.prepared_at).toLocaleTimeString() : ""}</div>
                  </div>
                  <div className="agent-card-body">
                    <div className="agent-avatar-circle" style={{ borderColor: "var(--blue)" }}>
                      {AVATAR_EMOJI[a.avatar] || "🐕"}
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
                    <button className="btn btn-sm" onClick={() => loadThread(a.task_id)}>
                      <MessageCircle size={12} /> Messages
                    </button>
                  </div>
                </div>
              ))}

              {activity.map((a, i) => (
                <div key={i} className="agent-card card">
                  <div className={`speech-bubble status-${a.status}`}>
                    {a.last_message || "No recent messages"}
                    <div className="speech-time">{a.last_seen ? new Date(a.last_seen).toLocaleTimeString() : ""}</div>
                  </div>
                  <div className="agent-card-body">
                    <div className="agent-avatar-circle" style={{ borderColor: a.status === "active" ? "var(--green)" : "var(--lemon)" }}>
                      🐕
                    </div>
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
                      <MessageCircle size={12} /> Messages
                    </button>
                    <button className="btn btn-sm"><Moon size={12} /> Sleep</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {selectedThread && (
            <div className="thread-panel card">
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
        </>
      )}

      {tab === "profiles" && (
        <div className="profiles-grid">
          {profiles.length === 0 ? (
            <div className="empty-state">
              <span style={{ fontSize: 48 }}>🏘️</span>
              <div className="empty-title">No agents in town yet</div>
              <div className="empty-sub">Create your first agent and watch them grow!</div>
            </div>
          ) : profiles.map((p) => (
            <div key={p.id} className="profile-card card">
              <div className="profile-header">
                <div className="profile-avatar-lg">
                  {AVATAR_EMOJI[p.avatar] || "🐕"}
                </div>
                <div className="profile-identity">
                  <div className="profile-name">{p.display_name}</div>
                  <div className="profile-rank">{RANK_LABELS[p.rank] || "🌱 Pup"}</div>
                  {p.flavor_text && <div className="profile-flavor">"{p.flavor_text}"</div>}
                </div>
              </div>

              <div className="profile-stats">
                <div className="profile-stat">
                  <span className="profile-stat-val" style={{ color: "var(--green)" }}>{p.tasks_completed}</span>
                  <span className="profile-stat-label">Completed</span>
                </div>
                <div className="profile-stat">
                  <span className="profile-stat-val" style={{ color: "var(--urgent)" }}>{p.tasks_failed}</span>
                  <span className="profile-stat-label">Failed</span>
                </div>
                <div className="profile-stat">
                  <span className="profile-stat-val" style={{ color: "var(--blue)" }}>{(p.success_rate * 100).toFixed(0)}%</span>
                  <span className="profile-stat-label">Success</span>
                </div>
                <div className="profile-stat">
                  <span className="profile-stat-val" style={{ color: "var(--lavender)" }}>{p.learnings_contributed}</span>
                  <span className="profile-stat-label">Learnings</span>
                </div>
              </div>

              {Object.keys(p.specializations || {}).length > 0 && (
                <div className="profile-specs">
                  <div className="profile-specs-label">Specializations</div>
                  {Object.entries(p.specializations).sort((a, b) => b[1] - a[1]).map(([repo, score]) => (
                    <div key={repo} className="profile-spec-row">
                      <span className="profile-spec-name">{repo}</span>
                      <div className="profile-spec-bar">
                        <div className="profile-spec-fill" style={{ width: `${score * 100}%` }} />
                      </div>
                      <span className="profile-spec-score">{(score * 100).toFixed(0)}%</span>
                    </div>
                  ))}
                </div>
              )}

              {p.last_active_at && (
                <div className="profile-footer">Last active: {new Date(p.last_active_at).toLocaleDateString()}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
