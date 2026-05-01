import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, FileText, MessageSquare, Send } from "lucide-react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { renderMarkdown } from "../utils/markdown";
import { formatTime } from "../utils/dates";
import PlanetSpinner from "../components/PlanetSpinner";
import "./ReviewPlan.css";

const CHAT_POLL_INTERVAL_MS = 8000;

/**
 * Unified report viewer for any AgentJob result that ISN'T a code
 * review — investigation findings, cartograph walks, skill outputs,
 * pack-owned one-shot runs.
 *
 * Code reviews keep their own diff-with-comments page (ReviewDiff.jsx)
 * because the diff + inline comments rail is a different shape than
 * "markdown report + chat thread".
 *
 * Reads the AgentJob row directly (job.artifact carries the markdown
 * output for jobs that finished via the lightweight specialty path,
 * and the existing AgentJob reply handler mirrors task-linked
 * artifacts onto the job too). The chat thread shares the same
 * inbox/messages endpoints as task-keyed agents — AgentMessage.task_id
 * holds either a task id or a job id depending on which the agent
 * reported with.
 */
export default function JobReport() {
  const { jobId } = useParams();
  const navigate = useNavigate();

  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [messages, setMessages] = useState([]);
  const [chatInput, setChatInput] = useState("");
  const [sendingChat, setSendingChat] = useState(false);
  const chatEndRef = useRef(null);

  const fetchJob = useCallback(async () => {
    setLoading(true);
    try {
      const j = await api.getAgentJob(jobId);
      setJob(j);
    } catch {
      setJob(null);
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  const fetchMessages = useCallback(async () => {
    try {
      const msgs = await api.getAgentMessages(jobId);
      setMessages(msgs || []);
    } catch {
      // Chat is non-critical; keep the report visible if messages
      // can't load (network blip, stale auth, etc.).
    }
  }, [jobId]);

  useEffect(() => { fetchJob(); }, [fetchJob]);
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
      await api.sendToAgent(jobId, { content: text, sender: "user" });
      setChatInput("");
      await fetchMessages();
    } catch (err) {
      showToast(err.message || "Couldn't send message", "high");
    } finally {
      setSendingChat(false);
    }
  };

  if (loading) {
    return (
      <div className="review-plan-page">
        <div className="review-plan-header">
          <PlanetSpinner size={14} /> Loading report…
        </div>
      </div>
    );
  }

  const artifact = job?.artifact;
  const hasArtifact = !!(artifact && artifact.trim());

  return (
    <div className="review-plan-page">
      <div className="review-plan-header">
        <button className="btn btn-sm" onClick={() => navigate(-1)}>
          <ArrowLeft size={10} /> Back
        </button>
        <div className="review-plan-title">
          <FileText size={14} /> {job?.title || `Job ${jobId}`}
          {job?.kind && <span className="review-plan-status approved">{job.kind}</span>}
          {job?.status && job.status !== "done" && (
            <span className="review-plan-status">{job.status}</span>
          )}
        </div>
      </div>

      {!hasArtifact ? (
        <div className="review-plan-empty">
          <p>
            {job?.status === "running"
              ? "Agent is still working — report will appear here when they finish."
              : job?.status === "failed"
                ? `Job failed${job?.error ? `: ${job.error}` : "."}`
                : "No report on this job yet."}
          </p>
        </div>
      ) : (
        <div className="review-plan-body">
          <div
            className="review-plan-content"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(artifact) }}
          />
        </div>
      )}

      {/* Channel log — back-and-forth with the agent for clarifications
          and follow-ups. Mirrors the chat surface on the plan page. */}
      <div className="review-plan-chat">
        <div className="review-plan-chat-header">
          <MessageSquare size={12} /> Chat with the agent
          <span className="review-plan-chat-hint">
            For follow-up questions and clarifications.
          </span>
        </div>
        <div className="review-plan-chat-thread">
          {messages.length === 0 ? (
            <div className="review-plan-chat-empty">
              No messages yet. Ask the agent a follow-up — they'll respond on their next check-in.
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
