import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Check, Loader, FileText, MessageSquare, Send } from "lucide-react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { renderMarkdown } from "../utils/markdown";
import { formatTime } from "../utils/dates";
import PlanetSpinner from "../components/PlanetSpinner";
import "./ReviewPlan.css";

const CHAT_POLL_INTERVAL_MS = 8000;

/**
 * Plan-approval page shown for tasks started in plan-first mode.
 * Agent produced a markdown plan via reply(message_type="plan_for_approval");
 * user either approves (agent resumes without plan mode to implement)
 * or asks for revisions (agent resumes in plan mode with feedback).
 */
export default function ReviewPlan() {
  const { taskId } = useParams();
  const navigate = useNavigate();

  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [task, setTask] = useState(null);
  const [revising, setRevising] = useState(false);
  const [approving, setApproving] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [messages, setMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [sendingChat, setSendingChat] = useState(false);
  const chatEndRef = useRef(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [p, t] = await Promise.all([
        api.getTaskPlan(taskId),
        api.getTask(taskId).catch(() => null),
      ]);
      setPlan(p);
      setTask(t);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  const fetchMessages = useCallback(async () => {
    try {
      const msgs = await api.getAgentMessages(taskId);
      setMessages(msgs || []);
    } catch {
      // silent — chat is non-critical, plan is still usable
    }
  }, [taskId]);

  useEffect(() => { fetchAll(); }, [fetchAll]);
  useEffect(() => {
    fetchMessages();
    const interval = setInterval(fetchMessages, CHAT_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchMessages]);

  // Scroll the chat to the newest message whenever the thread grows.
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  const handleSendChat = async () => {
    const text = chatInput.trim();
    if (!text || sendingChat) return;
    setSendingChat(true);
    try {
      await api.sendToAgent(taskId, { content: text, sender: "user" });
      setChatInput("");
      await fetchMessages();
    } catch (err) {
      showToast(err.message || "Couldn't send message", "high");
    } finally {
      setSendingChat(false);
    }
  };

  const handleApprove = async () => {
    if (approving) return;
    setApproving(true);
    try {
      await api.approveTaskPlan(taskId);
      showToast("Plan approved — agent is implementing now.", "normal");
      navigate(`/tasks`);
    } catch (err) {
      showToast(err.message || "Approve failed", "high");
    } finally {
      setApproving(false);
    }
  };

  const handleRevise = async () => {
    if (!feedback.trim() || revising) return;
    setRevising(true);
    try {
      await api.reviseTaskPlan(taskId, feedback);
      showToast("Revision requested — agent is updating the plan.", "normal");
      setFeedback("");
      navigate(`/tasks`);
    } catch (err) {
      showToast(err.message || "Revise failed", "high");
    } finally {
      setRevising(false);
    }
  };

  if (loading) {
    return (
      <div className="review-plan-page">
        <div className="review-plan-header">
          <PlanetSpinner size={14} /> Loading plan…
        </div>
      </div>
    );
  }

  const hasPlan = plan?.plan && plan.plan.trim();

  return (
    <div className="review-plan-page">
      <div className="review-plan-header">
        <button className="btn btn-sm" onClick={() => navigate(-1)}>
          <ArrowLeft size={10} /> Back
        </button>
        <div className="review-plan-title">
          <FileText size={14} /> {task?.title || `Task ${taskId}`}
          {plan?.plan_approved_at && <span className="review-plan-status approved">Approved</span>}
        </div>
      </div>

      {!hasPlan ? (
        <div className="review-plan-empty">
          <p>No plan yet — the agent is still working on it. Check back in a minute.</p>
        </div>
      ) : (
        <div className="review-plan-body">
          <div
            className="review-plan-content"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(plan.plan) }}
          />

          {!plan.plan_approved_at && (
            <div className="review-plan-actions">
              <div className="review-plan-revise">
                <div className="review-plan-revise-label">
                  <MessageSquare size={11} /> Request changes
                </div>
                <textarea
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder="What would you change about the plan? e.g. 'skip the caching layer, we don't need it yet' or 'break step 3 into smaller commits'"
                  rows={4}
                />
                <button
                  className="btn btn-sm"
                  onClick={handleRevise}
                  disabled={revising || !feedback.trim()}
                >
                  {revising ? <><Loader size={10} className="spin" /> Sending…</> : "Request revision"}
                </button>
              </div>
              <div className="review-plan-approve">
                <button
                  className="btn btn-primary"
                  onClick={handleApprove}
                  disabled={approving}
                >
                  {approving ? <><Loader size={10} className="spin" /> Starting…</> : <><Check size={12} /> Approve &amp; implement</>}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Channel log — quick back-and-forth with the agent that doesn't
          need a full plan revision (clarifications, "what about X?", etc).
          The agent picks these up via check_inbox on its next loop. */}
      <div className="review-plan-chat">
        <div className="review-plan-chat-header">
          <MessageSquare size={12} /> Chat with the agent
          <span className="review-plan-chat-hint">For quick clarifications. For major plan changes, use Request changes above.</span>
        </div>
        <div className="review-plan-chat-thread">
          {messages.length === 0 ? (
            <div className="review-plan-chat-empty">
              No messages yet. Say hi or ask the agent something — it'll respond on its next check-in.
            </div>
          ) : (
            messages.map((m) => (
              <div key={m.id} className={`review-plan-chat-msg ${m.direction}`}>
                <div className="review-plan-chat-msg-meta">
                  <span className="review-plan-chat-sender">{m.sender}</span>
                  <span className="review-plan-chat-type">{m.message_type}</span>
                  <span className="review-plan-chat-time">{formatTime(m.created_at)}</span>
                </div>
                <div className="review-plan-chat-content">{m.content}</div>
              </div>
            ))
          )}
          <div ref={chatEndRef} />
        </div>
        <div className="review-plan-chat-input">
          <input
            type="text"
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) handleSendChat(); }}
            placeholder="Send a message…"
            disabled={sendingChat}
          />
          <button
            className="btn btn-primary btn-sm"
            onClick={handleSendChat}
            disabled={sendingChat || !chatInput.trim()}
          >
            <Send size={11} /> Send
          </button>
        </div>
      </div>
    </div>
  );
}
