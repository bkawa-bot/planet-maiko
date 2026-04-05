import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  Bot, MessageCircle, Moon, Bone, GitBranch, CheckSquare,
  HeartPulse, Plus, Star, Trophy, X, ChevronRight, AlertTriangle,
  Flame, Play, Sparkles, Check, Clock, Zap, Target, TrendingUp,
} from "lucide-react";
import LeaderboardWidget from "../components/LeaderboardWidget";
import InfoButton from "../components/InfoButton";
import "./Agents.css";
import "./Gathering.css";


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
  const [showArchived, setShowArchived] = useState(false);
  const [selectedProfile, setSelectedProfile] = useState(null);

  // Pack Insights (Gathering) state
  const [eodState, setEodState] = useState(null);
  const [manualText, setManualText] = useState("");
  const [manualCategory, setManualCategory] = useState("domain_knowledge");
  const EOD_CATEGORIES = ["domain_knowledge", "pattern", "gotcha", "team"];

  const handleEodStart = async () => { await api.startPackInsights(); showToast("Gathering the pack...", "normal"); fetchEod(); };
  const handleEodCollect = async () => { await api.collectPackInsights(); showToast("Learnings collected!", "normal"); fetchEod(); };
  const handleEodSynthesize = async () => { await api.synthesizePackInsights(); showToast("Synthesizing...", "normal"); fetchEod(); };
  const handleEodFinalize = async () => { await api.finalizePackInsights({}); showToast("Merged into knowledge pool!", "normal"); fetchEod(); };
  const handleEodAdd = async () => {
    if (!manualText.trim()) return;
    await api.addPackInsightsLearning(manualText, manualCategory);
    setManualText("");
    fetchEod();
  };
  const fetchEod = async () => {
    try { setEodState(await api.getPackInsightsState()); } catch (err) { console.error(err); }
  };

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

  useEffect(() => { fetchData(); fetchEod(); }, []);

  const handleCreateAgent = async () => {
    try {
      const profile = await api.createProfile({});
      setShowArrival(profile);
      showToast(`${profile.display_name} just arrived in town! 🐾`, "normal");
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
              <div className="arrival-avatar"><Bot size={32} /></div>
              <h2 className="arrival-greeting">{showArrival.display_name}</h2>
              <p className="arrival-flavor">{showArrival.flavor_text}</p>
              <div className="arrival-rank">{RANK_LABELS[showArrival.rank] || "🌱 Pup"}</div>
              <button className="btn btn-primary" onClick={() => setShowArrival(null)}>
                Let's go!
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="inbox-tab-bar">
        <button className={`inbox-tab ${tab === "active" ? "active" : ""}`} onClick={() => setTab("active")}>Active</button>
        <button className={`inbox-tab ${tab === "profiles" ? "active" : ""}`} onClick={() => setTab("profiles")}>
          <Target size={10} /> Profiles
        </button>
        {tab === "profiles" && (
          <InfoButton title={<><Target size={16} /> Agent Profiles</>}>
            <p>Each agent is a <em>context set</em> — a personalized selection of learnings tuned for specific repos and task types.</p>
            <h4>Strengths</h4>
            <p>Categories where the agent scores above 70%. When an agent is strong in a category, those learnings get deprioritized in its brief — it's already mastered them, so the brief focuses on other areas.</p>
            <h4>Pup vs Senior</h4>
            <p>New agents ("pups") explore with random learnings and get an exploration bonus in recommendations. As they complete tasks, they specialize and rank up. Seniors exploit their proven learning sets.</p>
          </InfoButton>
        )}
        <button className={`inbox-tab ${tab === "insights" ? "active" : ""}`} onClick={() => setTab("insights")}>
          <Flame size={10} /> Pack Insights
        </button>
        {tab === "insights" && (
          <InfoButton title={<><Flame size={16} /> Pack Insights</>}>
            <p>A collaborative session where you and your agents share what you've learned.</p>
            <h4>The pipeline</h4>
            <ol>
              <li><strong>Start</strong> — signals agents to report their discoveries from recent work.</li>
              <li><strong>Collect</strong> — gathers feedback from agents, plus anything you add manually.</li>
              <li><strong>Synthesize</strong> — Maiko deduplicates, identifies what's already known, and proposes new rules.</li>
              <li><strong>Finalize</strong> — approved learnings merge into the Knowledge Pool and get used in future agent briefs.</li>
            </ol>
            <h4>When to use it</h4>
            <p>Run it after a productive session, at end of day, or whenever agents have been working on tasks. It's how the system gets smarter over time.</p>
          </InfoButton>
        )}
        <button className="btn btn-primary" onClick={handleCreateAgent} style={{ marginLeft: "auto" }}>
          <Plus size={12} /> New Agent
        </button>
      </div>

      {tab === "active" && (
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
                      {isResolved ? (
                        <span className="conflict-status-icon">✅</span>
                      ) : isDuplicate ? (
                        <span className="conflict-status-icon">⚠️</span>
                      ) : (
                        <AlertTriangle size={12} className="conflict-icon" />
                      )}
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
                    <div className="speech-time">{a.prepared_at ? new Date(a.prepared_at).toLocaleTimeString() : ""}</div>
                  </div>
                  <div className="agent-card-body">
                    <div className="agent-avatar-circle">
                      <Bot size={18} />
                    </div>
                    <div className="agent-info">
                      <div className="agent-name-row">
                        <span className="agent-name">{profiles.find(p => p.id === a.agent_id)?.display_name || a.agent_id?.replace("agent-", "")}</span>
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
                    <div className="agent-avatar-circle">
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
            <div className="modal-overlay" onClick={() => setSelectedThread(null)}>
              <div className="thread-modal" onClick={(e) => e.stopPropagation()}>
                <div className="modal-header">
                  <MessageCircle size={14} /> Thread: {selectedThread}
                  <span style={{ flex: 1 }} />
                  <button className="btn btn-sm" onClick={() => setSelectedThread(null)} style={{ border: "none", padding: 4 }}><X size={14} /></button>
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
            </div>
          )}
        </div>
        <div className="agents-active-sidebar">
          <LeaderboardWidget />
        </div>
        </div>
      )}

      {tab === "profiles" && (() => {
        const parseSpecs = (specs) => {
          if (!specs) return { byRepo: {}, strengths: [], gaps: [], allCategories: [] };
          const byRepo = {};
          for (const [key, score] of Object.entries(specs)) {
            const parts = key.split(":");
            const repo = parts[0] || "general";
            const cat = parts.slice(1).join(":") || "general";
            if (!byRepo[repo]) byRepo[repo] = {};
            byRepo[repo][cat] = score;
          }
          const all = Object.entries(specs).sort((a, b) => b[1] - a[1]);
          return {
            byRepo,
            allCategories: [...new Set(Object.values(byRepo).flatMap(Object.keys))],
            strengths: all.filter(([, s]) => s >= 0.7).slice(0, 5),
            gaps: all.filter(([, s]) => s < 0.3 && s > 0),
          };
        };

        const visibleProfiles = showArchived ? profiles : profiles.filter((p) => !p.archived);

        return (
          <div className="strategies-view">
            <div className="profiles-toolbar">
              <label className="show-archived-toggle">
                <input type="checkbox" checked={showArchived} onChange={async (e) => {
                  setShowArchived(e.target.checked);
                  const p = await api.getProfiles(e.target.checked ? { archived: "true" } : {});
                  setProfiles(p);
                }} />
                Show archived
              </label>
            </div>
            {visibleProfiles.length === 0 ? (
              <div className="empty-state">
                <Target size={36} className="empty-icon" />
                <div className="empty-title">No agent profiles yet</div>
                <div className="empty-sub">Create an agent to start building specialized context sets.</div>
                <button className="btn btn-primary" onClick={handleCreateAgent} style={{ marginTop: 12 }}>
                  <Plus size={12} /> Create First Agent
                </button>
              </div>
            ) : (
              <div className="strategies-grid">
                {visibleProfiles.map((p) => {
                  const specEntries = Object.entries(p.specializations || {}).sort((a, b) => b[1] - a[1]);
                  const repoCount = new Set(specEntries.map(([k]) => k.split(":")[0])).size;
                  const totalTasks = p.tasks_completed + p.tasks_failed;
                  const isPup = totalTasks < 3;

                  return (
                    <div key={p.id} className={`strategy-card card ${p.archived ? "archived" : ""}`}>
                      <div className="strategy-header">
                        <div className="strategy-avatar"><Bot size={20} /></div>
                        <div className="strategy-identity">
                          <div className="strategy-name">{p.display_name}</div>
                          <div className="strategy-meta">
                            <span className={`strategy-rank rank-${p.rank || "pup"}`}>{RANK_LABELS[p.rank] || "🌱 Pup"}</span>
                            {isPup && <span className="strategy-tag exploring"><Zap size={9} /> exploring</span>}
                            {p.archived && <span className="strategy-tag archived-tag">archived</span>}
                          </div>
                        </div>
                        <div className="strategy-score-ring">
                          <span className="strategy-score-val">{(p.success_rate * 100).toFixed(0)}%</span>
                          <span className="strategy-score-label">success</span>
                        </div>
                      </div>

                      {p.flavor_text && <div className="strategy-flavor">"{p.flavor_text}"</div>}

                      {specEntries.filter(([, s]) => s >= 0.7).length > 0 && (
                        <div className="strategy-section">
                          <div className="strategy-section-label"><TrendingUp size={10} /> Strengths</div>
                          <div className="strategy-chips">
                            {specEntries.filter(([, s]) => s >= 0.7).slice(0, 4).map(([key, score]) => (
                              <span key={key} className="strategy-chip strength">{key.split(":").pop()?.replace(/_/g, " ")} <span className="chip-score">{(score * 100).toFixed(0)}%</span></span>
                            ))}
                          </div>
                        </div>
                      )}

                      <div className="strategy-stats-row">
                        <span className="strategy-stat"><CheckSquare size={10} /> {p.tasks_completed} done</span>
                        <span className="strategy-stat"><X size={10} /> {p.tasks_failed} failed</span>
                        <span className="strategy-stat"><Target size={10} /> {repoCount} repo(s)</span>
                      </div>

                      <div className="strategy-card-actions">
                        {p.archived ? (
                          <button className="btn btn-sm" onClick={async () => {
                            await api.unarchiveProfile(p.id);
                            showToast(`${p.display_name} is back!`, "normal");
                            fetchData();
                          }}>Unarchive</button>
                        ) : (
                          <button className="btn btn-sm btn-danger" onClick={async () => {
                            await api.archiveProfile(p.id);
                            showToast(`${p.display_name} archived`, "normal");
                            fetchData();
                          }}><X size={10} /> Archive</button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

          </div>
        );
      })()}

      {tab === "insights" && (() => {
        const status = eodState?.status || "idle";
        return (
          <div className="gathering-panel">
            <div className="pipeline">
              {["idle", "gathering", "reviewing", "synthesized", "finalized"].map((step) => {
                const steps = ["idle", "gathering", "reviewing", "synthesized", "finalized"];
                const idx = steps.indexOf(step);
                const currentIdx = steps.indexOf(status);
                return (
                  <div key={step} className={`pipe-step ${idx === currentIdx ? "current" : ""} ${idx < currentIdx ? "done" : ""}`}>
                    {step}
                  </div>
                );
              })}
            </div>

            {status === "idle" && (
              <div className="panel-center">
                <p>Start a session to collect learnings from the pack</p>
                <button className="btn btn-primary" onClick={handleEodStart}>
                  <Flame size={12} /> Start Pack Insights
                </button>
              </div>
            )}

            {status === "gathering" && (
              <div className="panel-center">
                <p>Gathering signal sent. Click collect when agents have reported.</p>
                <button className="btn btn-primary" onClick={handleEodCollect}>
                  <Play size={12} /> Collect from Pack
                </button>
              </div>
            )}

            {status === "reviewing" && (
              <>
                <div className="add-learning-row">
                  <input value={manualText} onChange={(e) => setManualText(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleEodAdd()}
                    placeholder="What did you learn today?" />
                  <select value={manualCategory} onChange={(e) => setManualCategory(e.target.value)}>
                    {EOD_CATEGORIES.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
                  </select>
                  <button className="btn" onClick={handleEodAdd}><Check size={12} /></button>
                </div>
                <div className="learning-items">
                  {(eodState.collected || []).map((item, i) => (
                    <div key={i} className="learning-item">
                      <div className="learning-text">{item.text}</div>
                      <div className="learning-tags">
                        <span className="tag">{item.category?.replace(/_/g, " ")}</span>
                        {item.source_agent && <span className="tag">{item.source_agent}</span>}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="panel-footer">
                  <span className="count-text">{eodState.collected?.length || 0} learnings</span>
                  <button className="btn btn-primary" onClick={handleEodSynthesize}>
                    <Sparkles size={12} /> Synthesize
                  </button>
                </div>
              </>
            )}

            {status === "synthesized" && eodState.synthesis && (
              <>
                <div className="synthesis-report card" style={{ borderLeft: "3px solid var(--pink)" }}>
                  <div className="synthesis-header">Maiko's Synthesis</div>
                  <ul className="synthesis-bullets">
                    <li>{eodState.synthesis.duplicates_merged} duplicates merged</li>
                    <li>{eodState.synthesis.already_known?.length || 0} already known</li>
                    <li>{eodState.synthesis.unique_learnings?.length || 0} new learnings</li>
                    <li>{eodState.synthesis.proposed_rules?.length || 0} proposed rules</li>
                  </ul>
                </div>
                {eodState.synthesis.proposed_rules?.map((r, i) => (
                  <div key={i} className="card" style={{ borderLeft: "3px solid var(--blue)", marginTop: 8 }}>
                    <span className="tag">{r.category}</span>
                    <div style={{ marginTop: 4, fontSize: 12, color: "var(--text)" }}>{r.text}</div>
                  </div>
                ))}
                <div className="panel-footer" style={{ marginTop: 12 }}>
                  <button className="btn btn-primary" onClick={handleEodFinalize}>
                    <Check size={12} /> Finalize & Merge
                  </button>
                </div>
              </>
            )}

            {status === "finalized" && (
              <div className="panel-center">
                <Check size={24} style={{ color: "var(--green)" }} />
                <p>Learnings have been merged into the global knowledge pool.</p>
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
}
