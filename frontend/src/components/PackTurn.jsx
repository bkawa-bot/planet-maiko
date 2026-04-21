import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";

/**
 * Single dispatch turn from "Ask the pack" — shared between the
 * inline Home flow (PackAskBox) and the Cmd/Ctrl+K floating panel
 * (AskMaiko). Keeps rendering in one place so the two surfaces
 * stay visually consistent when we change something here.
 *
 * `turn` shapes:
 *   { kind: "user",       text, context?, nonGoals? }
 *   { kind: "clarify",    text }
 *   { kind: "error",      text }
 *   { kind: "dispatched", agent, task, message, reasoning, launchStatus }
 */
export default function PackTurn({ turn, onClose }) {
  if (turn.kind === "user") {
    return (
      <div className="ask-maiko-msg user">
        <div className="ask-maiko-msg-text">
          {turn.text}
          {turn.context && <div className="ask-pack-user-ctx">— {turn.context}</div>}
          {turn.nonGoals && (
            <div className="ask-pack-user-ctx ask-pack-user-nogoals">
              Must not: {turn.nonGoals}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (turn.kind === "clarify") {
    return (
      <div className="ask-maiko-msg maiko">
        <span className="ask-maiko-avatar">M</span>
        <div className="ask-maiko-msg-text">{turn.text}</div>
      </div>
    );
  }

  if (turn.kind === "error") {
    return (
      <div className="ask-maiko-msg maiko">
        <span className="ask-maiko-avatar">M</span>
        <div className="ask-maiko-msg-text ask-pack-error">
          Hmm, that didn't work. {turn.text}
        </div>
      </div>
    );
  }

  const { agent, task, message, launchStatus } = turn;
  const agentInitial = (agent?.display_name || "M").charAt(0).toUpperCase();

  return (
    <div className="ask-pack-card">
      <div className="ask-pack-card-head">
        <span className="ask-pack-agent-avatar">{agentInitial}</span>
        <div className="ask-pack-card-msg">{message}</div>
      </div>
      <div className="ask-pack-task-row">
        <div className="ask-pack-task-title">{task?.title}</div>
        <div className="ask-pack-task-meta">
          {launchStatus === "queued" && <span className="ask-pack-chip">queued</span>}
          {launchStatus === "kicked_off" && (
            <span className="ask-pack-chip ask-pack-chip-on">running</span>
          )}
        </div>
      </div>
      <Link
        to="/agents"
        className="ask-pack-open-link"
        onClick={onClose}
      >
        Open in Pack <ArrowRight size={11} />
      </Link>
    </div>
  );
}
